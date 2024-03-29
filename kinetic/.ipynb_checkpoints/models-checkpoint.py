import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from kinetic.custom_modules import *

class KineticsChannelModelFilterAmacrine(nn.Module):
    def __init__(self, bnorm=True, drop_p=0, scale_kinet=False, recur_seq_len=5, n_units=5, 
                 noise=0., bias=True, linear_bias=False, chans=[8,8], softplus=True, 
                 inference_exp=False, img_shape=(40,50,50), ksizes=(15,11), centers=None):
        super().__init__()
        
        self.n_units = n_units
        self.chans = chans 
        self.softplus = softplus 
        self.infr_exp = inference_exp 
        self.bias = bias 
        self.img_shape = img_shape 
        self.ksizes = ksizes 
        self.linear_bias = linear_bias 
        self.noise = noise
        self.centers = centers
        
        self.drop_p = drop_p
        self.scale_kinet = scale_kinet
        self.seq_len = recur_seq_len
        shape = self.img_shape[1:] # (H, W)
        self.shapes = []
        self.h_shapes = []

        modules = []
        modules.append(LinearStackedConv2d(self.img_shape[0],self.chans[0],
                                           kernel_size=self.ksizes[0], abs_bnorm=False, 
                                           bias=self.bias, drop_p=self.drop_p))
        shape = update_shape(shape, self.ksizes[0])
        self.shapes.append(tuple(shape))
        modules.append(GaussianNoise(std=self.noise))     
        modules.append(nn.ReLU())
        self.bipolar = nn.Sequential(*modules)

        modules = []
        modules.append(LinearStackedConv2d(self.chans[0],self.chans[1],
                                           kernel_size=self.ksizes[1], abs_bnorm=False, 
                                           bias=self.bias, drop_p=self.drop_p))
        shape = update_shape(shape, self.ksizes[1])
        self.shapes.append(tuple(shape))
        n_states = 4
        self.h_shapes.append((n_states, self.chans[1], shape[0]*shape[1]))
        self.h_shapes.append((self.chans[1], shape[0]*shape[1]))
        modules.append(GaussianNoise(std=self.noise))
        modules.append(nn.Sigmoid())
        modules.append(Reshape((-1, self.chans[1], shape[0] * shape[1])))
        self.amacrine = nn.Sequential(*modules)
        
        self.kinetics = Kinetics(chan=self.chans[1], dt=0.01)
        if scale_kinet:
            self.kinet_scale = ScaleShift((self.seq_len, self.chans[1], shape[0]*shape[1]))

        modules = []
        modules.append(Reshape((-1, self.seq_len, self.chans[1] * shape[0] * shape[1])))
        modules.append(Temperal_Filter(self.seq_len, 1))
        modules.append(nn.Linear(self.chans[1]*shape[0]*shape[1], 
                                 self.n_units, bias=self.linear_bias))
        modules.append(nn.Softplus())
        self.ganglion = nn.Sequential(*modules)

    def forward(self, x, hs):
        """
        x - FloatTensor (B, C, H, W)
        hs - list [(B,S,C,N),(D,B,C,N)]
            First list element should be a torch FloatTensor of state population values.
            Second element should be deque of activated population values over past D time steps
        """
        fx = self.bipolar(x)
        fx = self.amacrine(fx)
        fx, h0 = self.kinetics(fx, hs[0]) 
        hs[1].append(fx)
        h1 = hs[1]
        fx = torch.stack(list(h1), dim=1) #(B,D,N)
        if self.scale_kinet:
            fx = self.kinet_scale(fx)
        fx = self.ganglion(fx)
        return fx, [h0, h1]
    
class LNK(nn.Module):
    def __init__(self, name, dt=0.01, img_shape=(100,), ka_offset=False, ksr_gain=False, k_inits={}, **kwargs):
        super().__init__()
        
        self.name = name
        self.dt = dt
        self.filter_len = img_shape[0]
        
        self.ln_filter = Temperal_Filter(self.filter_len, 0)
        self.bias = nn.Parameter(torch.rand(1))
        self.nonlinear = nn.Sigmoid()
        self.kinetics = Kinetics(dt=self.dt, chan=1, ka_offset=ka_offset, ksr_gain=ksr_gain, **k_inits)
        self.scale_shift = nn.Linear(2, 1)
        self.spiking = nn.Softplus()
        n_states = 4
        self.h_shapes = (n_states, 1)
        self.k_inits = k_inits
        self.ka_offset = ka_offset
        self.ksr_gain = ksr_gain
    
    def forward(self, x, hs):
        out = self.ln_filter(x) + self.bias
        out = self.nonlinear(out)[:, None]
        out, hs_new = self.kinetics(out, hs)
        deriv = (hs_new[:, 1] - hs[:, 1]) / self.dt
        out = torch.cat((out, deriv), dim=1)
        out = self.scale_shift(out)
        out = self.spiking(out)
        return out, hs_new
    
class KineticsChannelModelDeriv(nn.Module):
    def __init__(self, bnorm=True, drop_p=0, recur_seq_len=5, n_units=5, 
                 noise=0., bias=True, linear_bias=False, chans=[8,8], softplus=True, 
                 inference_exp=False, img_shape=(40,50,50), ksizes=(15,11), dt=0.01, centers=None):
        super().__init__()
        
        self.kinetic = True
        self.n_units = n_units
        self.chans = chans 
        self.dt = dt
        self.softplus = softplus 
        self.infr_exp = inference_exp 
        self.bias = bias 
        self.img_shape = img_shape 
        self.ksizes = ksizes 
        self.linear_bias = linear_bias 
        self.noise = noise 
        self.centers = centers
        
        self.drop_p = drop_p
        self.seq_len = recur_seq_len
        shape = self.img_shape[1:] # (H, W)
        self.shapes = []

        modules = []
        modules.append(LinearStackedConv2d(self.img_shape[0],self.chans[0],
                                           kernel_size=self.ksizes[0], abs_bnorm=False, 
                                           bias=self.bias, drop_p=self.drop_p))
        shape = update_shape(shape, self.ksizes[0])
        self.shapes.append(tuple(shape))
        n_states = 4
        self.h_shapes = (n_states, self.chans[0], shape[0]*shape[1])
        modules.append(GaussianNoise(std=self.noise))     
        modules.append(nn.Sigmoid())
        modules.append(Reshape((-1, self.chans[0], shape[0] * shape[1])))
        self.bipolar = nn.Sequential(*modules)

        self.kinetics = Kinetics(chan=self.chans[0], dt=self.dt)
        
        self.w = nn.Parameter(torch.rand(self.chans[0], 1, 2))
        self.b = nn.Parameter(torch.rand(self.chans[0], 1))
        #self.w = nn.Parameter(torch.rand(1, 2))
        #self.b = nn.Parameter(torch.rand(1))
        modules = []
        modules.append(nn.ReLU())
        self.spiking_block = nn.Sequential(*modules)
        

        modules = []
        modules.append(Reshape((-1, self.chans[0], shape[0], shape[1])))
        modules.append(LinearStackedConv2d(self.chans[0], self.chans[1],
                                           kernel_size=self.ksizes[1], abs_bnorm=False, 
                                           bias=self.bias, drop_p=self.drop_p))
        shape = update_shape(shape, self.ksizes[1])
        self.shapes.append(tuple(shape))
        modules.append(Flatten())
        modules.append(GaussianNoise(std=self.noise))
        modules.append(nn.ReLU())
        self.amacrine = nn.Sequential(*modules)

        modules = []
        modules.append(nn.Linear(self.chans[1]*shape[0]*shape[1], 
                                 self.n_units, bias=self.linear_bias))
        modules.append(nn.Softplus())
        self.ganglion = nn.Sequential(*modules)
        
    def forward(self, x, hs):
        """
        x - FloatTensor (B, C, H, W)
        hs - list [(B,S,C,N),(D,B,C,N)]
            First list element should be a torch FloatTensor of state population values.
            Second element should be deque of activated population values over past D time steps
        """
        fx = self.bipolar(x)
        fx, hs_new = self.kinetics(fx, hs)
        deriv = (hs_new[:, 1] - hs[:, 1]) / self.dt
        fx = torch.stack((fx, deriv), dim=-1)
        fx = (self.w * fx).sum(-1) + self.b
        fx = self.spiking_block(fx)
        fx = self.amacrine(fx)
        fx = self.ganglion(fx)
        return fx, hs_new
    
class KineticsModel(nn.Module):
    def __init__(self, name, n_units=5, bias=True, linear_bias=False, chans=[8, 8], img_shape=(40, 50, 50), ksizes=(15, 11),
                 k_chan=True, ka_offset=False, ksr_gain=False, k_inits={}, dt=0.01, scale_shift_chan=True, **kwargs):
        super().__init__()
        
        self.name = name
        self.n_units = n_units
        self.chans = chans 
        self.dt = dt
        self.img_shape = img_shape 
        self.ksizes = ksizes 
        shape = self.img_shape[1:]
        self.shapes = []
        self.k_inits= k_inits
        self.k_chan = k_chan
        self.ka_offset = ka_offset
        self.ksr_gain = ksr_gain
        self.scale_shift_chan = scale_shift_chan

        modules = []
        modules.append(LinearStackedConv2d(self.img_shape[0], self.chans[0], kernel_size=self.ksizes[0], bias=bias))
        shape = update_shape(shape, self.ksizes[0])
        self.shapes.append(tuple(shape))
        
        modules.append(nn.Sigmoid())
        modules.append(Reshape((-1, self.chans[0], shape[0] * shape[1])))
        self.bipolar = nn.Sequential(*modules)
        
        n_states = 4
        self.h_shapes = (n_states, self.chans[0], shape[0]*shape[1])
        self.kinetics = Kinetics(dt=self.dt, chan=self.chans[0], ka_offset=ka_offset, ksr_gain=ksr_gain, k_chan=k_chan, **k_inits)
            
        if scale_shift_chan:
            self.kinetics_w = nn.Parameter(torch.rand(self.chans[0], 1))
            self.kinetics_b = nn.Parameter(torch.rand(self.chans[0], 1))
        else:
            self.kinetics_w = nn.Parameter(torch.rand(1))
            self.kinetics_b = nn.Parameter(torch.rand(1))
            
        modules = []
        modules.append(nn.ReLU())
        self.spiking_block = nn.Sequential(*modules)
        

        modules = []
        modules.append(Reshape((-1, self.chans[0], shape[0], shape[1])))
        modules.append(LinearStackedConv2d(self.chans[0], self.chans[1], kernel_size=self.ksizes[1], bias=bias))
        shape = update_shape(shape, self.ksizes[1])
        self.shapes.append(tuple(shape))
        modules.append(Flatten())
        modules.append(nn.ReLU())
        self.amacrine = nn.Sequential(*modules)

        modules = []
        modules.append(nn.Linear(self.chans[1] * shape[0] * shape[1], self.n_units, bias=linear_bias))
        modules.append(nn.Softplus())
        self.ganglion = nn.Sequential(*modules)
        
    def forward(self, x, hs):
        """
        x - FloatTensor (B, C, H, W)
        hs - (B,S,C,N) or (B,S,1,N)
        """
        fx = self.bipolar(x)
        fx, hs = self.kinetics(fx, hs)
        fx = self.kinetics_w * fx + self.kinetics_b
        fx = self.spiking_block(fx)
        fx = self.amacrine(fx)
        fx = self.ganglion(fx)
        return fx, hs
    
class KineticsOnePixel(nn.Module):
    def __init__(self, name, n_units=5, bias=True, linear_bias=False, chans=[8, 8], img_shape=(40, ),
                 k_chan=True, ka_offset=False, ksr_gain=False, k_inits={}, dt=0.01, scale_shift_chan=True, **kwargs):
        super().__init__()
        
        self.name = name
        self.n_units = n_units
        self.chans = chans 
        self.bias = bias 
        self.img_shape = img_shape 
        self.linear_bias = linear_bias 
        self.dt = dt
        self.h_shapes = []
        self.k_inits= k_inits
        self.k_chan = k_chan
        self.ka_offset = ka_offset
        self.ksr_gain = ksr_gain
        self.scale_shift_chan = scale_shift_chan

        self.bipolar_weight = nn.Parameter(torch.rand(self.chans[0], self.img_shape[0]))
        self.bipolar_bias = nn.Parameter(torch.rand(self.chans[0]))

        n_states = 4
        self.h_shapes = (n_states, self.chans[0], 1)
        self.kinetics = Kinetics(dt=self.dt, chan=self.chans[0], ka_offset=ka_offset, ksr_gain=ksr_gain, k_chan=k_chan, **k_inits)
            
            
        if scale_shift_chan:
            self.kinetics_w = nn.Parameter(torch.rand(self.chans[0], 1))
            self.kinetics_b = nn.Parameter(torch.rand(self.chans[0], 1))
        else:
            self.kinetics_w = nn.Parameter(torch.rand(1))
            self.kinetics_b = nn.Parameter(torch.rand(1))
            
        modules = []
        modules.append(nn.ReLU())
        self.spiking_block = nn.Sequential(*modules)
        
        self.amacrine_weight = nn.Parameter(torch.rand(self.chans[1], self.chans[0]))
        self.amacrine_bias = nn.Parameter(torch.rand(self.chans[1]))

        modules = []
        modules.append(nn.Linear(self.chans[1], self.n_units, bias=self.linear_bias))
        modules.append(nn.Softplus())
        self.ganglion = nn.Sequential(*modules)
        
    def forward(self, x, hs):
        """
        x - FloatTensor (B, C)
        hs - (B,S,C,1)
        
        """
        
        fx = (self.bipolar_weight * x[:,None]).sum(dim=-1) + self.bipolar_bias
        fx = F.sigmoid(fx)[:,:,None] #(B,C,1)
        fx, hs = self.kinetics(fx, hs)
        fx = self.kinetics_w * fx + self.kinetics_b
        fx = self.spiking_block(fx).squeeze(-1)
        fx = (self.amacrine_weight * fx[:,None]).sum(dim=-1) + self.amacrine_bias
        fx = F.relu(fx)
        fx = self.ganglion(fx)
        return fx, hs
    
class KineticsModel1D(nn.Module):
    def __init__(self, name, n_units=5, bias=True, linear_bias=False, chans=[8, 8], img_shape=(40, 50, 50), ksizes=(15, 11),
                 k_chan=True, ka_offset=False, ksr_gain=False, k_inits={}, dt=0.01, scale_shift_chan=True, **kwargs):
        super().__init__()
        
        self.name = name
        self.n_units = n_units
        self.chans = chans 
        self.dt = dt
        self.img_shape = img_shape 
        self.ksizes = ksizes 
        shape = self.img_shape[1:]
        self.shapes = []
        self.k_inits= k_inits
        self.k_chan = k_chan
        self.ka_offset = ka_offset
        self.ksr_gain = ksr_gain
        self.scale_shift_chan = scale_shift_chan

        modules = []
        modules.append(nn.Conv1d(self.img_shape[0], self.chans[0], kernel_size=self.ksizes[0], bias=bias))
        shape = update_shape(shape, self.ksizes[0])
        self.shapes.append(tuple(shape))
        
        modules.append(nn.Sigmoid())
        modules.append(Reshape((-1, self.chans[0], shape[0])))
        self.bipolar = nn.Sequential(*modules)
        
        n_states = 4
        self.h_shapes = (n_states, self.chans[0], shape[0])
        self.kinetics = Kinetics(dt=self.dt, chan=self.chans[0], ka_offset=ka_offset, ksr_gain=ksr_gain, k_chan=k_chan, **k_inits)
            
        if scale_shift_chan:
            self.kinetics_w = nn.Parameter(torch.rand(self.chans[0], 1))
            self.kinetics_b = nn.Parameter(torch.rand(self.chans[0], 1))
        else:
            self.kinetics_w = nn.Parameter(torch.rand(1))
            self.kinetics_b = nn.Parameter(torch.rand(1))
            
        modules = []
        modules.append(nn.ReLU())
        self.spiking_block = nn.Sequential(*modules)
        

        modules = []
        modules.append(Reshape((-1, self.chans[0], shape[0])))
        modules.append(nn.Conv1d(self.chans[0], self.chans[1], kernel_size=self.ksizes[1], bias=bias))
        shape = update_shape(shape, self.ksizes[1])
        self.shapes.append(tuple(shape))
        modules.append(Flatten())
        modules.append(nn.ReLU())
        self.amacrine = nn.Sequential(*modules)

        modules = []
        modules.append(nn.Linear(self.chans[1] * shape[0], self.n_units, bias=linear_bias))
        modules.append(nn.Softplus())
        self.ganglion = nn.Sequential(*modules)
        
    def forward(self, x, hs):
        """
        x - FloatTensor (B, C, H, W)
        hs - (B,S,C,N) or (B,S,1,N)
        """
        fx = self.bipolar(x)
        fx, hs = self.kinetics(fx, hs)
        fx = self.kinetics_w * fx + self.kinetics_b
        fx = self.spiking_block(fx)
        fx = self.amacrine(fx)
        fx = self.ganglion(fx)
        return fx, hs
    
class KineticsModelSen(nn.Module):
    def __init__(self, name, n_units=5, bias=True, linear_bias=False, chans=[8, 8], img_shape=(40, 50, 50), ksizes=(15, 11),
                 k_chan=True, ka_offset=False, ksr_gain=False, k_inits={}, dt=0.01, scale_shift_chan=True, **kwargs):
        super().__init__()
        
        self.name = name
        self.n_units = n_units
        self.chans = chans 
        self.dt = dt
        self.img_shape = img_shape 
        self.ksizes = ksizes 
        shape = self.img_shape[1:]
        self.shapes = []
        self.k_inits= k_inits
        self.k_chan = k_chan
        self.ka_offset = ka_offset
        self.ksr_gain = ksr_gain
        self.scale_shift_chan = scale_shift_chan

        modules = []
        modules.append(LinearStackedConv2d(self.img_shape[0], self.chans[0], kernel_size=self.ksizes[0], bias=bias))
        shape = update_shape(shape, self.ksizes[0])
        self.shapes.append(tuple(shape))
        
        modules.append(Reshape((-1, self.chans[0], shape[0] * shape[1])))
        modules.append(ScaleShift(shape=(self.chans[0], 1)))
        self.bipolar = nn.Sequential(*modules)
        
        self.bipolar_nl = nn.Sigmoid()
        
        modules = []
        modules.append(nn.Conv2d(self.img_shape[0], 1, kernel_size=self.ksizes[0], bias=bias))
        modules.append(nn.Sigmoid())
        modules.append(Reshape((-1, 1, shape[0] * shape[1])))
        self.bipolar_inh = nn.Sequential(*modules)
        
        self.kinetics_inh = Kinetics(dt=self.dt, chan=1, ka_offset=ka_offset, ksr_gain=ksr_gain, k_chan=k_chan, **k_inits)
        
        n_states = 4
        self.h_shapes = (n_states, self.chans[0], shape[0]*shape[1])
        self.kinetics = Kinetics(dt=self.dt, chan=self.chans[0], ka_offset=ka_offset, ksr_gain=ksr_gain, k_chan=k_chan, **k_inits)
            
        self.kinetics_w = nn.Parameter(torch.rand(self.chans[0], 1))
        self.kinetics_b = nn.Parameter(torch.rand(self.chans[0], 1))
        
        self.kinetics_w_inh = nn.Parameter(torch.rand(self.chans[0], 1))
        self.kinetics_b_inh = nn.Parameter(torch.rand(self.chans[0], 1))
            
        self.spiking_block1 = nn.ReLU()
        self.spiking_block2 = nn.ReLU()

        modules = []
        modules.append(Reshape((-1, self.chans[0], shape[0], shape[1])))
        modules.append(LinearStackedConv2d(self.chans[0], self.chans[1], kernel_size=self.ksizes[1], bias=bias))
        shape = update_shape(shape, self.ksizes[1])
        self.shapes.append(tuple(shape))
        modules.append(Flatten())
        modules.append(nn.ReLU())
        self.amacrine = nn.Sequential(*modules)

        modules = []
        modules.append(nn.Linear(self.chans[1] * shape[0] * shape[1], self.n_units, bias=linear_bias))
        modules.append(nn.Softplus())
        self.ganglion = nn.Sequential(*modules)
        
    def forward(self, x, hs):
        """
        x - FloatTensor (B, C, H, W)
        hs - (B,S,C,N) or (B,S,1,N)
        """
        fx = self.bipolar(x)
        inh = self.bipolar_inh(x)
        inh, hs2 = self.kinetics_inh(inh, hs[1])
        inh = self.kinetics_w_inh * inh + self.kinetics_b_inh
        inh = self.spiking_block1(inh)
        fx = fx - inh
        fx = self.bipolar_nl(fx)
        fx, hs1 = self.kinetics(fx, hs[0])
        fx = self.kinetics_w * fx + self.kinetics_b
        fx = self.spiking_block2(fx)
        fx = self.amacrine(fx)
        fx = self.ganglion(fx)
        return fx, (hs1, hs2)
    
class KineticsModelSenConv(nn.Module):
    def __init__(self, name, n_units=5, bias=True, linear_bias=False, chans=[8, 8], img_shape=(40, 50, 50), ksizes=(15, 11),
                 k_chan=True, ka_offset=False, ksr_gain=False, k_inits={}, dt=0.01, scale_shift_chan=True, **kwargs):
        super().__init__()
        
        self.name = name
        self.n_units = n_units
        self.chans = chans 
        self.dt = dt
        self.img_shape = img_shape 
        self.ksizes = ksizes 
        shape = self.img_shape[1:]
        self.shapes = []
        self.k_inits= k_inits
        self.k_chan = k_chan
        self.ka_offset = ka_offset
        self.ksr_gain = ksr_gain
        self.scale_shift_chan = scale_shift_chan

        modules = []
        modules.append(LinearStackedConv2d(self.img_shape[0], self.chans[0], kernel_size=self.ksizes[0], bias=bias))
        shape = update_shape(shape, self.ksizes[0])
        self.shapes.append(tuple(shape))
        
        modules.append(Reshape((-1, self.chans[0], shape[0] * shape[1])))
        modules.append(ScaleShift(shape=(self.chans[0], 1)))
        self.bipolar = nn.Sequential(*modules)
        
        self.bipolar_nl = nn.Sigmoid()
        
        modules = []
        modules.append(nn.Conv2d(self.img_shape[0], 1, kernel_size=self.ksizes[0], bias=bias))
        modules.append(nn.Sigmoid())
        modules.append(Reshape((-1, 1, shape[0] * shape[1])))
        self.bipolar_inh = nn.Sequential(*modules)
        
        self.kinetics_inh = Kinetics(dt=self.dt, chan=1, ka_offset=ka_offset, ksr_gain=ksr_gain, k_chan=k_chan, **k_inits)
        
        n_states = 4
        self.h_shapes = (n_states, self.chans[0], shape[0]*shape[1])
        self.kinetics = Kinetics(dt=self.dt, chan=self.chans[0], ka_offset=ka_offset, ksr_gain=ksr_gain, k_chan=k_chan, **k_inits)
            
        if scale_shift_chan:
            self.kinetics_w = nn.Parameter(torch.rand(self.chans[0], 1))
            self.kinetics_b = nn.Parameter(torch.rand(self.chans[0], 1))
        else:
            self.kinetics_w = nn.Parameter(torch.rand(1))
            self.kinetics_b = nn.Parameter(torch.rand(1))
        
        self.kinetics_w_inh = nn.Parameter(torch.rand(self.chans[0], 1))
        self.kinetics_b_inh = nn.Parameter(torch.rand(self.chans[0], 1))
            
        self.spiking_block1 = nn.ReLU()
        self.spiking_block2 = nn.ReLU()

        modules = []
        modules.append(Reshape((-1, self.chans[0], shape[0], shape[1])))
        modules.append(LinearStackedConv2d(self.chans[0], self.chans[1], kernel_size=self.ksizes[1], bias=bias))
        shape = update_shape(shape, self.ksizes[1])
        self.shapes.append(tuple(shape))
        modules.append(nn.ReLU())
        self.amacrine = nn.Sequential(*modules)

        modules = []
        modules.append(nn.Conv2d(self.chans[1], 1, kernel_size=self.ksizes[1], bias=linear_bias))
        modules.append(nn.Softplus())
        self.ganglion = nn.Sequential(*modules)
        
    def forward(self, x, hs):
        """
        x - FloatTensor (B, C, H, W)
        hs - (B,S,C,N) or (B,S,1,N)
        """
        fx = self.bipolar(x)
        inh = self.bipolar_inh(x)
        inh, hs2 = self.kinetics_inh(inh, hs[1])
        inh = self.kinetics_w_inh * inh + self.kinetics_b_inh
        inh = self.spiking_block1(inh)
        fx = fx - inh
        fx = self.bipolar_nl(fx)
        fx, hs1 = self.kinetics(fx, hs[0])
        fx = self.kinetics_w * fx + self.kinetics_b
        fx = self.spiking_block2(fx)
        fx = self.amacrine(fx)
        fx = self.ganglion(fx)
        return fx, (hs1, hs2)