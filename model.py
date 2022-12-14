# -*- coding: utf-8 -*-
"""Untitled10.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1RDx5mZR5TanLa4gqL_g39HItuyvp7qZQ
"""

import torch
import torch.nn as nn
import torch.utils.data as Data
import torch.optim as optim
import torch.nn.functional as F
import numpy as np

import sys
import os
file = ''
curPath = os.path.abspath(os.path.dirname(file))
rootPath = os.path.split(curPath)[0]
sys.path.append(rootPath)

class GCN_Layer(nn.Module):
    def __init__(self,num_of_features,num_of_filter):
        """One layer of GCN
        
        Arguments:
            num_of_features {int} -- the dimension of node feature
            num_of_filter {int} -- the number of graph filters
        """
        super(GCN_Layer,self).__init__()
        self.gcn_layer = nn.Sequential(
            nn.Linear(in_features = num_of_features,
                    out_features = num_of_filter),
            nn.ReLU()
        )
    def forward(self,input,adj):
        """GCN
        
        Arguments:
            input {Tensor} -- signal matrix,shape (batch_size,N,T*D)
            adj {np.array} -- adjacent matrix，shape (N,N)
        Returns:
            {Tensor} -- output,shape (batch_size,N,num_of_filter)
        """
        batch_size,_,_ = input.shape
        adj = torch.from_numpy(adj).to(input.device)
        adj = adj.repeat(batch_size,1,1)
        input = torch.bmm(adj, input)
        output = self.gcn_layer(input)
        return output

class STGeoModule(nn.Module):
    def __init__(self,grid_in_channel,num_of_gru_layers,seq_len,
                gru_hidden_size,num_of_target_time_feature):
        """[summary]
        
        Arguments:
            grid_in_channel {int} -- the number of grid data feature (batch_size,T,D,W,H),grid_in_channel=D
            num_of_gru_layers {int} -- the number of GRU layers
            seq_len {int} -- the time length of input
            gru_hidden_size {int} -- the hidden size of GRU
            num_of_target_time_feature {int} -- the number of target time feature，为24(hour)+7(week)+1(holiday)=32
        """
        super(STGeoModule,self).__init__()
        self.grid_conv = nn.Sequential(
            nn.Conv2d(in_channels=grid_in_channel,out_channels=64,kernel_size=3,padding=1),
            nn.ReLU(),
            nn.Conv2d(in_channels=64,out_channels=grid_in_channel,kernel_size=3,padding=1),
            nn.ReLU(),
        )

        self.grid_gru = nn.GRU(grid_in_channel,gru_hidden_size,num_of_gru_layers,batch_first=True)

        self.grid_att_fc1 = nn.Linear(in_features=gru_hidden_size,out_features=1)
        self.grid_att_fc2 = nn.Linear(in_features=num_of_target_time_feature,out_features=seq_len)
        self.grid_att_bias = nn.Parameter(torch.zeros(1))
        self.grid_att_softmax = nn.Softmax(dim=-1)


    def forward(self,grid_input,target_time_feature):
        """
        Arguments:
            grid_input {Tensor} -- grid input，shape：(batch_size,seq_len,D,W,H)
            target_time_feature {Tensor} -- the feature of target time，shape：(batch_size,num_target_time_feature)
        Returns:
            {Tensor} -- shape：(batch_size,hidden_size,W,H)
        """
        batch_size,T,D,W,H = grid_input.shape
        
        grid_input = grid_input.view(-1,D,W,H)
        conv_output = self.grid_conv(grid_input)

        conv_output = conv_output.view(batch_size,-1,D,W,H)\
                        .permute(0,3,4,1,2)\
                        .contiguous()\
                        .view(-1,T,D)
        gru_output,_ = self.grid_gru(conv_output)
        
        grid_target_time = torch.unsqueeze(target_time_feature,1).repeat(1,W*H,1).view(batch_size*W*H,-1)
        grid_att_fc1_output = torch.squeeze(self.grid_att_fc1(gru_output))
        grid_att_fc2_output = self.grid_att_fc2(grid_target_time)
        grid_att_score = self.grid_att_softmax(F.relu(grid_att_fc1_output+grid_att_fc2_output+self.grid_att_bias))
        grid_att_score = grid_att_score.view(batch_size*W*H,-1,1)
        grid_output = torch.sum(gru_output * grid_att_score,dim=1)
        
        grid_output = grid_output.view(batch_size,W,H,-1).permute(0,3,1,2).contiguous()
    
        return grid_output



class GSNet(nn.Module):
    def __init__(self,grid_in_channel,num_of_gru_layers,seq_len,pre_len,
                gru_hidden_size,num_of_target_time_feature,
                num_of_graph_feature,nums_of_graph_filters,
                north_south_map,west_east_map):
        """[summary]
        
        Arguments:
            grid_in_channel {int} -- the number of grid data feature (batch_size,T,D,W,H),grid_in_channel=D
            num_of_gru_layers {int} -- the number of GRU layers
            seq_len {int} -- the time length of input
            pre_len {int} -- the time length of prediction
            gru_hidden_size {int} -- the hidden size of GRU
            num_of_target_time_feature {int} -- the number of target time feature，为24(hour)+7(week)+1(holiday)=32
            num_of_graph_feature {int} -- the number of graph node feature，(batch_size,seq_len,D,N),num_of_graph_feature=D
            nums_of_graph_filters {list} -- the number of GCN output feature
            north_south_map {int} -- the weight of grid data
            west_east_map {int} -- the height of grid data
        """
        super(GSNet,self).__init__()
        self.north_south_map = north_south_map
        self.west_east_map = west_east_map

        self.st_geo_module = STGeoModule(grid_in_channel,num_of_gru_layers,seq_len,gru_hidden_size,num_of_target_time_feature)
        fusion_channel = 16
        self.grid_weigth = nn.Conv2d(in_channels=gru_hidden_size,out_channels=fusion_channel,kernel_size=1)
        self.graph_weigth = nn.Conv2d(in_channels=gru_hidden_size,out_channels=fusion_channel,kernel_size=1)
        self.output_layer = nn.Linear(fusion_channel*north_south_map*west_east_map,pre_len*north_south_map*west_east_map)


    def forward(self,grid_input,target_time_feature,graph_feature,
                road_adj,risk_adj,poi_adj,grid_node_map):
        """
        Arguments:
            grid_input {Tensor} -- grid input，shape：(batch_size,T,D,W,H)
            graph_feature {Tensor} -- Graph signal matrix，(batch_size,T,D1,N)
            target_time_feature {Tensor} -- the feature of target time，shape：(batch_size,num_target_time_feature)
            road_adj {np.array} -- road adjacent matrix，shape：(N,N)
            risk_adj {np.array} -- risk adjacent matrix，shape：(N,N)
            grid_node_map {np.array} -- map graph data to grid data,shape (W*H,N)
        Returns:
            {Tensor} -- shape：(batch_size,pre_len,north_south_map,west_east_map)
        """
        batch_size,_,_,_,_ =grid_input.shape

        grid_output = self.st_geo_module(grid_input,target_time_feature)
        graph_output = self.st_sem_module(graph_feature,road_adj,risk_adj,target_time_feature,grid_node_map)
        
        grid_output = self.grid_weigth(grid_output)
        graph_output = self.graph_weigth(graph_output)
        fusion_output = (grid_output + graph_output).view(batch_size,-1)
        final_output = self.output_layer(fusion_output).view(batch_size,-1,self.north_south_map,self.west_east_map)
        return final_output