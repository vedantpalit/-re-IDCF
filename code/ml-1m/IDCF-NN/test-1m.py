import os
import time
import random
import pickle
import numpy as np
import pandas as pd
import argparse
import yaml
from model import IRMC_NN_Model, NNMFModel
from utils import *
from datetime import datetime
import torch

def fix_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
fix_seed(1234)

parser = argparse.ArgumentParser(description='PMF')
parser.add_argument('--gpus', default='0', help='gpus')
parser.add_argument('--extra', action="store_true", help='whether extra or inter')
args = parser.parse_args()

config = yaml.safe_load(open("/Users/vedantpalit/Desktop/IDCF-master/code/ml-1m/IDCF-NN/datainfo.yaml", 'r'))

os.environ["CUDA_VISIBLE_DEVICES"] = args.gpus
device = torch.device('cpu')

LEARNING_RATE = 0.001
DECAYING_FACTOR = 1.
LAMBDA_REG = 0.05
BATCH_SIZE_TRAIN = 1024
BATCH_SIZE_TEST = 128
HIS_MAXLEN = 100
HIS_SAMPLE_NUM = 20
n_epochs = 1 # 500

DATASET = 'ml-1m'
SPLIT_WAY = 'threshold'
THRESHOLD = 70
SUPP_RATIO = 0.8
TRAINING_RATIO = 1.0
EXTRA = args.extra
datadir = '/Users/vedantpalit/Desktop/IDCF-master/data/'
n_user = config[DATASET]['n_user']
n_item = config[DATASET]['n_item']

train_set_supp, train_set_que, test_set_supp, test_set_que, user_supp_list, user_his_dic = \
generate_data(datadir=datadir, 
				dataset=DATASET, 
				split_way=SPLIT_WAY,
				supp_ratio=SUPP_RATIO, 
				threshold=THRESHOLD,
				training_ratio=TRAINING_RATIO)

user_supp_num = len(user_supp_list)
user_que_num = n_user - user_supp_num

supp_users = torch.tensor(user_supp_list, dtype = torch.long)
supp_users_mask = torch.zeros(n_user)
supp_users_mask[supp_users] = 1
if SPLIT_WAY == 'all':
	train_set_supp = torch.tensor(train_set_supp + train_set_que)
else:
	train_set_supp = torch.tensor(train_set_supp)
train_set_que = torch.tensor(train_set_que)
test_set_supp = torch.tensor(test_set_supp)
test_set_que = torch.tensor(test_set_que)

def sequence_adjust(seq):
	seq_new = seq
	if len(seq) <= 0:
		seq_new = [np.random.randint(0, n_item) for i in range(HIS_SAMPLE_NUM)]
	if len(seq) > HIS_MAXLEN:
		random.shuffle(seq)
		seq_new = seq[:HIS_MAXLEN]
	return seq_new

def train(model, optimizer, i, supp_or_que):
	model.train()
	optimizer.zero_grad()
	
	if supp_or_que == 'supp':
		train_set_supp_i = train_set_supp[i*BATCH_SIZE_TRAIN : (i+1)*BATCH_SIZE_TRAIN]
		train_set_supp_i_x = train_set_supp_i[:, :2].long().to(device)
		train_set_supp_i_y = train_set_supp_i[:, 2].float().to(device)
		pred_y = model(train_set_supp_i_x)
		loss_r = torch.sum((train_set_supp_i_y - pred_y) ** 2)
		loss_reg = model.regularization_loss()
		loss = loss_r + LAMBDA_REG * loss_reg
	else:
		train_set_que_i = train_set_que[i*BATCH_SIZE_TRAIN : (i+1)*BATCH_SIZE_TRAIN]
		train_set_i_x = train_set_que_i[:, :2].long().to(device)
		train_set_i_y = train_set_que_i[:, 2].float().to(device)
		train_set_his_i = [torch.tensor(
		sequence_adjust( user_his_dic[train_set_que_i[k][0].item()] ),
		dtype = torch.long
		)   for k in range(train_set_que_i.size(0))]
		train_set_hl_i = [train_set_his_i[k].size(0) for k in range(train_set_que_i.size(0))]
		train_set_his_i = torch.nn.utils.rnn.pad_sequence(train_set_his_i, batch_first = True, padding_value = 0.).to(device)
		train_set_hl_i = torch.tensor(train_set_hl_i, dtype=torch.long).to(device)
		pred_y = model(train_set_i_x, train_set_his_i, train_set_hl_i)
		loss = torch.sum((train_set_i_y - pred_y) ** 2)
		
	loss.backward()

def test(model, test_set, supp_or_que):
	model.eval()
	l1_sum, l2_sum, ndcg_sum, num = 0., 0., 0., 0
	test_size = test_set.size(0)
	user_score_dict, user_label_dict = {}, {}
	for k in user_his_dic.keys():
		user_score_dict[k] = []
		user_label_dict[k] = []
	for i in range(test_size // BATCH_SIZE_TEST + 1):
		with torch.no_grad():
			test_set_i = test_set[i*BATCH_SIZE_TEST : (i+1)*BATCH_SIZE_TEST]
			test_set_i_x = test_set_i[:, :2].long().to(device)
			test_set_i_y = test_set_i[:, 2].float().to(device)
			test_set_his_i = [torch.tensor(
				sequence_adjust( user_his_dic[test_set_i[k][0].item()] ),
				dtype = torch.long
				)   for k in range(test_set_i.size(0))]
			test_set_hl_i = [test_set_his_i[k].size(0) for k in range(test_set_i.size(0))]
			test_set_his_i = torch.nn.utils.rnn.pad_sequence(test_set_his_i, batch_first = True, padding_value = 0.).to(device)
			test_set_hl_i = torch.tensor(test_set_hl_i, dtype=torch.long).to(device)

			if supp_or_que == 'supp':
				pred_y = model(test_set_i_x)
			else:
				pred_y = model(test_set_i_x, test_set_his_i, test_set_hl_i)
		y_hat, y = pred_y.cpu().numpy(), test_set_i_y.cpu().numpy()
		l1_sum += np.sum( np.abs(y_hat - y) )
		l2_sum += np.sum( np.square(y_hat - y) )
		for k in range(test_set_i.size(0)):
			u, s, y = test_set_i_x[k, 0].item(), pred_y[k].item(), test_set_i_y[k].item()
			user_score_dict[u] += [s]
			user_label_dict[u] += [y]
	MAE = l1_sum / test_size
	RMSE = np.sqrt( l2_sum / test_size )
	for k in user_score_dict.keys():
		if len(user_score_dict[k]) <= 1:
			continue
		ndcg_sum += ndcg_k(user_score_dict[k], user_label_dict[k], len(user_score_dict[k]))
		num += 1
	return MAE, RMSE, ndcg_sum, num

def load_model_s(model, path):
	model.load_model(path+'model.pkl')

def load_model_q(model, path):
	if EXTRA:
		model.load_model(path + 'model-extra.pkl')
	else:
		model.load_model(path+'model-inter.pkl')

if EXTRA:
	model_q = IRMC_NN_Model(n_user=n_user,
							n_item=n_item,
							supp_users=supp_users,
							device=device).to(device)
	load_model_q(model_q, '/Users/vedantpalit/Desktop/IDCF-master/code/ml-1m/IDCF-NN/train-1m/')
	MAE_q, RMSE_q, ndcg_sum_q, num_q = test(model_q, test_set_que, supp_or_que='que')
	NDCG_q = ndcg_sum_q / num_q
	log = 'Que Test Result: MAE: {:.4f} RMSE: {:.4f} NDCG: {:.4f}'.format(MAE_q, RMSE_q, NDCG_q)
	print(log)
else:
	model_s = NNMFModel(n_user = n_user,
					n_item = n_item).to(device)
	load_model_s(model_s, '/Users/vedantpalit/Desktop/IDCF-master/code/ml-1m/IDCF-NN/pretrain-1m/')
	MAE_s, RMSE_s, ndcg_sum_s, num_s = test(model_s, test_set_supp, supp_or_que='supp')
	NDCG_s = ndcg_sum_s / num_s
	log = 'Key Test Result: MAE: {:.4f} RMSE: {:.4f} NDCG: {:.4f}'.format(MAE_s, RMSE_s, NDCG_s)
	print(log)

	model_q = IRMC_NN_Model(n_user = n_user,
					n_item = n_item,
					supp_users = supp_users,
					device = device).to(device)
	load_model_q(model_q, '/Users/vedantpalit/Desktop/IDCF-master/code/ml-1m/IDCF-NN/train-1m/')
	MAE_q, RMSE_q, ndcg_sum_q, num_q  = test(model_q, test_set_que, supp_or_que='que')
	NDCG_q = ndcg_sum_q / num_q
	log = 'Que Test Result: MAE: {:.4f} RMSE: {:.4f} NDCG: {:.4f}'.format(MAE_q, RMSE_q, NDCG_q)
	print(log)

	supp_size, que_size = test_set_supp.size(0), test_set_que.size(0)
	MAE = ( MAE_s * supp_size + MAE_q * que_size )/ (supp_size+que_size)
	RMSE = np.sqrt( (RMSE_s**2 * supp_size + RMSE_q**2 * que_size) / (supp_size+que_size))
	NDCG = (ndcg_sum_q + ndcg_sum_s) / (num_q + num_s)
	log = 'All Test Result: MAE: {:.4f} RMSE: {:.4f} NDCG: {:.4f}'.format(MAE, RMSE, NDCG)
	print(log)
