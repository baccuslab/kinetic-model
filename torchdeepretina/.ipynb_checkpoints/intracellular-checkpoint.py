import matplotlib
from scipy.stats import sem, pearsonr
from tqdm import tqdm
import os
import re
import h5py
import collections
import pyret.filtertools as ft
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, FormatStrFormatter
import torchdeepretina.utils as tdrutils
import torchdeepretina.stimuli as tdrstim

centers = {
        "bipolars_late_2012":[  (19, 22), (18, 20), (20, 21), (18, 19)],
        "bipolars_early_2012":[ (17, 23), (17, 23), (18, 23)],
        "amacrines_early_2012":[(18, 23), (19, 24), (19, 23), (19, 23), (18, 23)],
        "amacrines_late_2012":[ (20, 20), (22, 19), (20, 20), (19, 22), (22, 25),
                                (19, 21), (19, 17), (20, 19), (17, 20), (19, 23),
                                (17, 20), (17, 20), (20, 19), (18, 18), (19, 17),
                                (17, 17), (18, 20), (20, 19), (17, 19), (19, 18),
                                (17, 17), (25, 15)
                              ]
}

#If you want to use stimulus that isnt just boxes
def prepare_stim(stim, stim_type):
    """
    stim: ndarray
    stim_type: str
        preparation method
    """
    if stim_type == 'boxes':
        return 2*stim - 1
    elif stim_type == 'flashes':
        stim = stim.reshape(stim.shape[0], 1, 1)
        return np.broadcast_to(stim, (stim.shape[0], 38, 38))
    elif stim_type == 'movingbar':
        stim = block_reduce(stim, (1,6), func=np.mean)
        stim = pyret.stimulustools.upsample(stim.reshape(stim.shape[0], stim.shape[1], 1), 5)[0]
        return np.broadcast_to(stim, (stim.shape[0], stim.shape[1], stim.shape[1]))
    elif stim_type == 'lines':
        fxn = lambda m: np.convolve(m,0.5*np.ones((2,)),mode='same')
        stim_averaged = np.apply_along_axis(fxn, axis=1, arr=stim)
        stim = stim_averaged[:,::2]
        # now stack stimulus to convert 1d to 2d spatial stimulus
        return stim.reshape(-1,1,stim.shape[-1]).repeat(stim.shape[-1], axis=1)
    else:
        print("Invalid stim type")
        assert False

def load_interneuron_data(root_path, files=None, filter_length=40, stim_keys={"boxes"}, 
                                                                    join_stims=False,
                                                                    trunc_join=True):
    """ 
    Load data
    num_pots (number of potentials) stores the number of cells per stimulus
    mem_pots (membrane potentials) stores the membrane potential
    psst, you can find the "data" folder in /home/grantsrb on deepretina server if you need

    join_stims: bool
       combines the stimuli listed in stim_keys
    trunc_join: bool
       truncates the joined stimuli to be of equal length. Only applies if join_stims is true.

    returns:
    if using join_stims then no stim_type key exists
    stims - dict
        keys are the cell files, vals are dicts
            keys of subdicts are stim type with vals of ndarray stimuli (T,H,W)
    mem_pots - dict
        keys are the cell files, vals are dicts
            keys of subdicts are stim type with values of ndarray membrane potentials 
            for each cell within the file (N_CELLS, T-filter_length)
    """
    if files is None:
        files = ['bipolars_late_2012.h5', 'bipolars_early_2012.h5', 'amacrines_early_2012.h5', 
                'amacrines_late_2012.h5', 'horizontals_early_2012.h5', 'horizontals_late_2012.h5']
    files = [os.path.expanduser(os.path.join(root_path, name)) for name in files]
    file_ids = []
    for f in files:
        file_ids.append(re.split('_|\.', f)[0])
    num_pots = []
    stims = dict()
    mem_pots = dict()
    for fi in files:
        stims[fi] = None if join_stims else dict()
        mem_pots[fi] = None if join_stims else dict()
        if join_stims:
            shapes = []
            mem_shapes = []
        with h5py.File(fi,'r') as f:
            for k in f.keys():
                if k in stim_keys:
                    if join_stims:
                        shapes.append(prepare_stim(np.asarray(f[k+'/stimuli']), k).shape)
                        mem_pot = np.asarray(f[k+'/detrended_membrane_potential'])
                        mem_shapes.append(mem_pot.shape)
                        del mem_pot
                    else:
                        try:
                            temp = np.asarray(f[k+'/stimuli'], dtype=np.float32)
                            stims[fi][k] = prepare_stim(temp, k)
                            temp = np.asarray(f[k]['detrended_membrane_potential'])
                            mem_pots[fi][k] = temp[:, filter_length:].astype(np.float32)
                            del temp
                        except Exception as e:
                            print(e)
                            print("stim error at", k)
            if join_stims:

                # Summing up length of first dimension of all stimuli
                if trunc_join:
                    trunc_len = np.min([s[0] for s in shapes])
                    zero_dim = [trunc_len*len(shapes)]
                else:
                    zero_dim=[s[0] for i,s in enumerate(shapes)]
                one_dim = [s[1] for s in shapes]
                two_dim = [s[2] for s in shapes]
                shape = [np.sum(zero_dim), np.max(one_dim), np.max(two_dim)]
                stims[fi] = np.empty(shape, dtype=np.float32)

                zero_dim = [s[0] for s in mem_shapes] # Number of cells
                mem_shape = [np.max(zero_dim), shape[0]-filter_length]
                mem_pots[fi] = np.empty(mem_shape, dtype=np.float32)

                startx = 0
                mstartx = 0
                for i,k in enumerate(stim_keys):
                    prepped = prepare_stim(np.asarray(f[k+'/stimuli']), k)
                    if trunc_join:
                        prepped = prepped[:trunc_len]
                    # In case stim have varying spatial dimensions
                    if not (prepped.shape[-2] == stims[fi].shape[-2] and 
                                        prepped.shape[-1] == stims[fi].shape[-1]):
                        prepped = tdrstim.spatial_pad(prepped,stims[fi].shape[-2],
                                                                stims[fi].shape[-1])
                    endx = startx+len(prepped)
                    stims[fi][startx:endx] = prepped
                    mem_pot = np.asarray(f[k]['detrended_membrane_potential'])
                    if trunc_join:
                        mem_pot = mem_pot[:,:trunc_len]
                    if i == 0:
                        mem_pot = mem_pot[:,filter_length:]
                    mendx = mstartx+mem_pot.shape[1]
                    mem_pots[fi][:,mstartx:mendx] = mem_pot
                    startx = endx
                    mstartx = mendx
    return stims, mem_pots, files

# Functions for correlation maps and loading David's stimuli in deep retina models.
def pad_to_edge(stim):
    '''
    Pads the spatial dimensions of a stimulus to be 50x50 for deep retina models.
    
    Args:
        stim: (time, space, space) numpy array.
    Returns:
        padded_stim: (time, space, space) padded numpy array.
    '''
    padded_stim = np.zeros((stim.shape[0], 50, 50))
    height = stim.shape[1]
    width = stim.shape[2]
    
    if height >= 50 or width >= 50: # Height must be less than 50.
        return stim.copy()
    
    y_start = int((50 - height)/2.0)
    x_start = int((50 - width)/2.0)
    
    padded_stim[:, y_start:y_start+height, x_start:x_start+width] = stim
    return padded_stim

def correlation_map(mem_pot, model_layer):
    '''
    Takes a 1d membrane potential and computes the correlation with every tiled 
    unit in model_layer.
    
    Args:
        mem_pot: 1-d numpy array
        model_layer: (time, space, space) layer of activities
    '''
    height = model_layer.shape[-2]
    width = model_layer.shape[-1]
    correlations = np.zeros((height, width))
    for y in range(height):
        for x in range(width):
            #adjusted_layer = model_layer[:,y,x]/(np.max(model_layer[:,y,x])+1e-40)
            #r,_ = pearsonr(mem_pot, adjusted_layer)
            r,_ = pearsonr(mem_pot.squeeze(),model_layer[:,y,x].squeeze())
            correlations[y,x] = r if not np.isnan(r) and r < 1 and r > -1 else 0
    return correlations

def max_correlation(mem_pot, model_layer, abs_val=False):
    '''
    Takes a 1d membrane potential and computes the correlation with every tiled unit 
    in model_layer. Do not use abs_val for publishable analysis.
    
    Args:
        mem_pot: 1-d numpy array, membrane potential
        model_layer: (time, celltype, space, space) layer of activities
        abs_val: take absolute value of correlation
    '''
    n_chans = model_layer.shape[1]
    if len(model_layer.shape) > 2:
        cor_maps = [correlation_map(mem_pot,model_layer[:,c]) for c in range(n_chans)]
        if abs_val:
            cor_maps = [np.absolute(m) for m in cor_maps]
        return np.max([np.max(m) for m in cor_maps])
    else:
        pearsons = []
        for chan in range(model_layer.shape[1]):
            r,_ = pearsonr(mem_pot, model_layer[:,chan])
            pearsons.append(r)
        # Set nan and fishy values to zero
        pearsons = [r if not np.isnan(r) and r < 1 and r > -1 else 0 for r in pearsons]
        if abs_val:
            pearsons = [np.absolute(r) for r in pearsons]
        return np.max(pearsons)

def sorted_correlation(mem_pot, model_layer):
    '''
    Takes a 1d membrane potential and computes the maximum correlation with respect to each model celltype,
    sorted from highest to lowest.
    
    Args:
        mem_pot: 1-d numpy array
        model_layer: (time, celltype, space, space) layer of activities
    '''
    if len(model_layer.shape) > 2:
        n_chans = model_layer.shape[1]
        return sorted(
            [np.max(correlation_map(mem_pot, model_layer[:,c])) for c in range(n_chans)])
    else:
        #adjusted_layer = model_layer/(np.max(model_layer)+1e-40)
        #pearsons = [pearsonr(mem_pot, adjusted_layer)[0] for c in range(model_layer.shape[1])]
        pearsons = [pearsonr(mem_pot, model_layer[:,c])[0] for c in range(model_layer.shape[1])]
        pearsons = [r if not np.isnan(r) and r < 1  and r > -1 else 0 for r in pearsons]
        return sorted(pearsons)

def max_correlation_all_layers(mem_pot, model_response, layer_keys=['conv1', 'conv2'], abs_val=False):
    '''
    Takes a 1d membrane potential and computes the maximum correlation over the 2 conv layers within a model.
    Args: 
        mem_pot: 1-d numpy array
        model_response: a dict of layer activities
        layer_keys: keys of model_response to be tested
        abs_val: use absolute value of correlations
    '''
    max_cors = [max_correlation(mem_pot, model_response[k], abs_val=abs_val) for k in layer_keys]
    return max(max_cors)

def argmax_correlation_recurse_helper(mem_pot, model_layer, shape, idx, abs_val=False):
    """
    Recursively searches model_layer units to find the unit with the best pearsonr.

    mem_pot: membrane potential ndarray (N,)
    model_layer: ndarray (N,C) or (N,C,H,W)
    shape: list
    idx: int

    Returns:
        best_idx: tuple (chan, row, col)
            most correlated idx
    """
    if len(shape) == 0: # base case
        layer = model_layer[:,idx[0]]
        # Does nothing if model_layer was originally (time, celltype) dims
        # Otherwise sequentially widdles layer down to 1 dimension
        for i in idx[1:]: 
            # Sequentially selects dims of the model resulting in layer[:,idx[1],idx[2]...]
            layer = layer[:,i] 
        r, _ = pearsonr(mem_pot, layer)
        if abs_val:
            r = np.absolute(r)
        if np.isnan(r) or r >= 1 or r <= -1: r = 0
        return r, idx
    else:
        max_r = -1
        best_idx = None
        for i in range(shape[0]):
            args = (mem_pot, model_layer, shape[1:], (*idx, i), abs_val)
            r, local_idx = argmax_correlation_recurse_helper(*args)
            if not np.isnan(r) and r < 1 and r > max_r:
                max_r = r
                best_idx = local_idx
        return max_r, best_idx

def argmax_correlation(mem_pot, model_layer, ret_max_cor=False, abs_val=False):
    '''
    Takes a 1d membrane potential and computes the correlation with every tiled unit 
    in model_layer.
    
    Args:
        mem_pot: nd array (T,)
        model_layer: (T, C, H, W) or (T, D) layer of activities
        ret_max_cor: returns value of max correlation in addition to idx
        abs_val: use absolute value of correlations
    Returns:
        best_idx: tuple (chan, row, col) or (unit,)
        max_r: float
    '''
    assert len(model_layer.shape) >= 2
    max_r = -1
    best_idx = None
    for i in range(model_layer.shape[1]):
        r, idx = argmax_correlation_recurse_helper(mem_pot,model_layer,
                                        model_layer.shape[2:],(i,), abs_val=abs_val)
        if not np.isnan(r) and r < 1 and r > max_r:
            max_r = r
            best_idx = idx
    if ret_max_cor:
        return best_idx, max_r
    return best_idx

def get_intr_cors(model, stim_dict, mem_pot_dict, layers={"sequential.2", "sequential.8"}, 
                                                            batch_size=500, verbose=False):
    """
    model - torch Module
    stim_dict - dict of interneuron stimuli
        keys: str (interneuron data file name)
            vals: dict
                keys: stim_types
                    vals: ndarray (T, H, W)
    mem_pot_dict - dict of interneuron membrane potentials
        keys: str (interneuron data file name)
            vals: dict
                keys: stim_types
                    vals: ndarray (CI, T)
                        CI is cell idx and T is time
    returns:
        intr_cors - dict
                - cell_file: list
                - cell_idx: list
                - stim_type: list
                - cell_type: list
                - cor: list
                - layer: list
                - chan: list
                - row: list
                - col: list
    """
    intr_cors = {
        "cell_file":[], 
        "cell_idx":[],
        "stim_type":[],
        "cell_type":[],
        "layer":[],
        "chan":[],
        "row":[],
        "col":[],
        "cor":[]
    }
    layers = sorted(list(layers))
    for cell_file in stim_dict.keys():
        for stim_type in stim_dict[cell_file].keys():
            stim = tdrstim.spatial_pad(stim_dict[cell_file][stim_type],model.img_shape[1])
            stim = tdrstim.rolling_window(stim, model.img_shape[0])
            if verbose:
                temp = cell_file.split("/")[-1].split(".")[0]
                cellstim = "cell_file:{}, stim_type:{}...".format(temp, stim_type)
                print("Collecting model response for "+cellstim)
            response = tdrutils.inspect(model, stim, insp_keys=layers, batch_size=batch_size,
                                                                               to_numpy=True)
            pots = mem_pot_dict[cell_file][stim_type]
            rnge = range(len(pots))
            if verbose:
                print("Correlating with data...")
                rnge = tqdm(rnge)
            best_cors = []
            for cell_idx in rnge:
                best_cor = -1
                for layer in layers:
                    resp = response[layer]
                    if len(resp.shape) == 2 and layer == "sequential.2":
                        resp = resp.reshape(-1, model.chans[0], *model.shapes[0])
                    elif len(resp.shape) == 2 and layer == "sequential.8":
                        resp = resp.reshape(-1, model.chans[1], *model.shapes[1])

                    for chan in range(resp.shape[1]):
                        r, idx = argmax_correlation_recurse_helper(pots[cell_idx], resp,
                                                       shape=resp.shape[2:], idx=(chan,))
                        _, row, col = idx
                        intr_cors['cell_file'].append(cell_file)
                        intr_cors['cell_idx'].append(cell_idx)
                        intr_cors['stim_type'].append(stim_type)
                        cell_type = cell_file.split("/")[-1].split("_")[0][:-1]
                        intr_cors['cell_type'].append(cell_type) # amacrine or bipolar
                        intr_cors['cor'].append(r)
                        intr_cors['layer'].append(layer)
                        intr_cors['chan'].append(chan)
                        intr_cors['row'].append(row)
                        intr_cors['col'].append(col)
                        if r > best_cor:
                            best_cor = r
                best_cors.append(best_cor)
            if verbose:
                print("Avg:", np.mean(best_cors))
    return intr_cors

def get_cor_generalization(model, stim_dict, mem_pot_dict,layers={"sequential.2","sequential.8"},
                                                                  batch_size=500, verbose=False):
    """
    Collects the interneuron correlation of each channel of each layer with each membrane 
    potential. Also collects the correlation of each of the best correlated units with the 
    other stimuli. Acts as a measure of generalization to the cell response regardless of 
    stimulus type.

    model - torch Module
    stim_dict - dict of interneuron stimuli
        keys: str (interneuron data file name)
            vals: dict
                keys: stim_types (must be more than one!!)
                    vals: ndarray (T, H, W)
    mem_pot_dict - dict of interneuron membrane potentials
        keys: str (interneuron data file name)
            vals: dict
                keys: stim_types (must be more than one!!)
                    vals: ndarray (CI, T)
                        CI is cell idx and T is time
    returns:
        For each entry, there should be a corresponding entry matching in all fields except
        for stim_type and cor.
        intr_cors - dict
                - cell_file: list
                - cell_idx: list
                - stim_type: list
                - cor: list
                - layer: list
                - chan: list
                - row: list
                - col: list
    """
    table = {"cell_file":[], "cell_idx":[], "stim_type":[], "cor":[], 
                            "layer":[], "chan":[], "row":[], "col":[]}
    layers = sorted(list(layers))
    for cell_file in stim_dict.keys():
        responses = dict()
        # Get all responses
        for stim_type in stim_dict[cell_file].keys():
            stim = tdrstim.spatial_pad(stim_dict[cell_file][stim_type],model.img_shape[1])
            stim = tdrstim.rolling_window(stim, model.img_shape[0])
            response = tdrutils.inspect(model, stim, insp_keys=layers, batch_size=batch_size)
            # Fix response shapes
            for l,layer in enumerate(layers):
                resp = response[layer]
                if len(resp.shape) <= 2:
                    resp = resp.reshape(-1, model.chans[l], *model.shapes[l])
                response[layer] = resp
            responses[stim_type] = response
        for stim_type in stim_dict[cell_file].keys():
            pots = mem_pot_dict[cell_file][stim_type]
            for cell_idx in range(len(pots)):
                for l,layer in enumerate(layers):
                    if verbose:
                        name = cell_file.split("/")[-1]
                        print("Evaluating file:{}, stim:{}, idx:{}, layer:{}".format(name, 
                                                                 stim_type,cell_idx,layer))
                    resp = responses[stim_type][layer]
                    for chan in range(resp.shape[1]):
                        r, idx = argmax_correlation_recurse_helper(pots[cell_idx], resp,
                                                       shape=resp.shape[2:], idx=(chan,))
                        _, row, col = idx
                        table['cell_file'].append(cell_file)
                        table['cell_idx'].append(cell_idx)
                        table['stim_type'].append(stim_type)
                        table["cor"].append(r)
                        table['layer'].append(layer)
                        table['chan'].append(chan)
                        table['row'].append(row)
                        table['col'].append(col)

                        for stim_t in stim_dict[cell_file].keys():
                            if stim_t != stim_type:
                                pot = mem_pot_dict[cell_file][stim_t][cell_idx]
                                temp_resp = responses[stim_t][layer][:,chan,row,col]
                                r,_ = pearsonr(temp_resp, pot)
                                table['cell_file'].append(cell_file)
                                table['cell_idx'].append(cell_idx)
                                table['stim_type'].append(stim_t)
                                table["cor"].append(r)
                                table['layer'].append(layer)
                                table['chan'].append(chan)
                                table['row'].append(row)
                                table['col'].append(col)
    return table

def get_correlation_stats(mem_pot, model_response, 
                                layer_keys=['sequential.2', 'sequential.8'], abs_val=False):
    """
    Finds the unit of maximum correlation for each channel in each of the argued layers.
    i.e. will return a (row,col) coordinate for each channel in each layer in layer_keys.

    mem_pot: ndarray (T,)
    model_response: dict
        keys: must contain each value in layer_keys. 
        values: ndarray (T,C,H,W)
    layer_keys: sequence of keys
        these are the keys to get the responses from model_response
    abs_val: bool
        if true, the returned maximum correlations are an absolute value of
        the actual correlation.

    Returns a dict of correlation statistics. Each layer_key is a key with a 
    list of correlation stats for each channel.
        {
            layer_key[0]: [(row,col,correlation)]
        }
    """
    cor_stats = dict()
    for layer_key in layer_keys:
        model_layer = model_response[layer_key]
        assert len(model_layer.shape) >= 3
        cor_stats[layer_key] = []
        for chan in range(model_layer.shape[1]):
            r, idx = argmax_correlation_recurse_helper(mem_pot,model_layer,
                                                        shape=model_layer.shape[2:],
                                                        idx=(chan,), abs_val=abs_val)
            _, row, col = idx
            cor_stats[layer_key].append((row,col,r))
    return cor_stats

def argmax_correlation_all_layers(mem_pot, model_response, 
                    layer_keys=['conv1', 'conv2'], ret_max_cor_all_layers=False, abs_val=False):
    '''
    Takes a 1d membrane potential and computes the maximum correlation over the 2 conv 
    layers within a model.

    Args: 
        mem_pot: 1-d numpy array
        model_response: a dict of layer activities
        layer_keys: keys of model_response to be tested
        abs_val: use absolute value of correlations
    returns:
        best_idx: (layer, chan, row, col)
    '''
    max_r = -1
    best_idx = None
    for key in layer_keys:
        response = model_response[key]
        idxs, r = argmax_correlation(mem_pot, response, ret_max_cor=True, 
                                                                    abs_val=abs_val)
        if not np.isnan(r) and r < 1 and r > max_r:
            max_r = r
            best_idx = (key, *idxs)
    if ret_max_cor_all_layers:
        return best_idx, max_r
    return best_idx # (layer, chan, row, col)

def classify(mem_pot, model_response, time, layer_keys=['conv1', 'conv2'], 
                                                                        abs_val=False):
    '''
    Finds the most correlated cell in a model to a membrane potential.

    Args:
        mem_pot: 1-d numpy array
        model_response: dict of activity at each layer of model
        time: the time to take into consideration
        layer_keys: keys of model_response to be tested
        abs_val: use absolute value of correlations
    Returns:
        a tuple with the layer, celltype, spatial indices, and the correlation value
    '''
    model_response_time = {k:model_response[k][:time] for k in layer_keys}
    best_cell, max_cor_all_layers = argmax_correlation_all_layers(mem_pot[:time], model_response_time, layer_keys=layer_keys, ret_max_cor_all_layers=True, abs_val=abs_val)
    if len(best_cell) == 2:
        return best_cell[0], best_cell[1], max_cor_all_layers
    return best_cell[0], best_cell[1], (best_cell[2], best_cell[3]), max_cor_all_layers

def classify_subtypes(mem_pot, model_response, time, layer_keys=['conv1', 'conv2']):
    '''
    Finds the highest correlation for each subtype in each layer.
    Args:
        mem_pot: 1-d numpy array
        model_response: dict of activity at each layer of model
        time: time up to which to consider
        layer_keys: keys of model_response to be tested
    Returns:
        the correlations as an array
    '''
    correlations = [np.max(correlation_map(mem_pot[:time], model_response[k][:time,c])) for c in range(model_response[k].shape[1])]
    return correlations

def plot_max_correlations(mem_pot, model_response):
    '''
    Plots the top correlations of celltypes over time
    mem_pot: 1-d numpy array
    model_response: dict of activity at each layer of model
    num_cells: the number of celltypes to plot
    '''
    y_0 = []
    y_1 = []
    y_2 = []
    classified_subtypes = classify_subtypes(mem_pot, model_response, mem_pot.shape[0])
    top_cell_subtypes = np.argsort(classified_subtypes)[-3:]
    for time in range(100, 1000, 100):
        y_0.append(classify_subtypes(mem_pot, model_response, time)[top_cell_subtypes[0]])
        y_1.append(classify_subtypes(mem_pot, model_response, time)[top_cell_subtypes[1]])
        y_2.append(classify_subtypes(mem_pot, model_response, time)[top_cell_subtypes[2]])
    plt.plot(y_0)
    plt.plot(y_1)
    plt.plot(y_2)
    plt.show()

def create_rfs(stimulus, mem_pot, model_cell_response, filter_length, cell_info, index):
    '''
    Creates plots of cell and model rfs side by side
    Args:
        stimulus: stimulus presented to cell and model, 3-d numpy array
        mem_pot: 1-d numpy array
        model_cell_response: 1-d numpy array
        cell_info: the information of the cell returned from classify
        filter_length: filter length
        index: the index of the cell 1-34. 1-6 are bipolar cells, the rest are amacrine.
    '''
    stim_size = stimulus.shape[0]
    stim_tmp = stimulus[filter_length:]
    model_title = "Model:{0}, {1}".format(cell_info[0], cell_info[1])
    if index < 7:
        cell_title = "bipolar cell"
    else:
        cell_title = "amacrine cell"
    rc_model, lags_model = ft.revcorr(scipy.stats.zscore(stimulus)[filter_length:], model_cell_response, nsamples_before=0, nsamples_after=filter_length)
    rc_cell, lags_cell = ft.revcorr(scipy.stats.zscore(stimulus)[filter_length:], mem_pot, nsamples_before=filter_length)
    spatial_model, temporal_model = ft.decompose(rc_model)
    spatial_cell, temporal_cell = ft.decompose(rc_cell)
    plt.subplot(2,2,1)
    img =plt.imshow(spatial_model, cmap = 'seismic', clim=[-np.max(abs(spatial_model)), np.max(abs(spatial_model))])
    plt.title(model_title)
    plt.subplot(2,2,2)
    img =plt.imshow(spatial_cell, cmap = 'seismic', clim=[-np.max(abs(spatial_cell)), np.max(abs(spatial_cell))])
    plt.title(cell_title)
    plt.subplot(2,2,3)
    plt.plot(temporal_model)
    plt.subplot(2,2,4)
    plt.plot(temporal_cell)
    fname = 'natural_scene_results/{0}.png'.format(index)
    plt.savefig(fname)
    plt.clf()
    
def plot_responses(mem_pot, model_cell_response):
    '''
    Plots the activity of the most correlated cell and membrane potential against each other.
    '''
    plt.subplot(1,2,1)
    plt.plot(model_cell_response)
    plt.subplot(1,2,2)
    plt.plot(mem_pot)


