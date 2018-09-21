#!/usr/bin/env python


import chainer
import chainer.functions as F
import chainer.links as L
from chainer import training
from chainer.training import extensions
import numpy as np
import os.path
import six
from chainer.datasets import tuple_dataset
from tqdm import tqdm
import shutil
import random
import cnn_model
import xml_cnn_model

USE_CUDNN = 'never' ## always, auto, or never

def select_function(scores):
    scores = chainer.cuda.to_cpu(scores)
    np_predicts = np.zeros(scores.shape,dtype=np.int8)
    for i in tqdm(range(len(scores)),desc="select labels based on threshold loop"):
        np_predicts[i] = (scores[i] >= 0.5)
    return np_predicts

def set_seed_random(seed):
        random.seed(seed)
        np.random.seed(seed)
        if chainer.cuda.available:
            chainer.cuda.cupy.random.seed(seed)

def main(params):
    print("")   
    print('# gpu: {}'.format(params["gpu"]))
    print('# unit: {}'.format(params["unit"]))
    print('# batch-size: {}'.format(params["batchsize"]))
    print('# epoch: {}'.format(params["epoch"]))
    print('# number of category: {}'.format(params["output-dimensions"]))
    print('# embedding dimension: {}'.format(params["embedding-dimensions"]))
    print('# current layer: {}'.format(params["currentDepth"]))
    print('# model-type: {}'.format(params["model-type"]))
    print('')


    f = open('./CNN/LOG/configuration_' + params["currentDepth"] + '.txt', 'w')
    f.write('# gpu: {}'.format(params["gpu"])+"\n")
    f.write('# unit: {}'.format(params["unit"])+"\n")
    f.write('# batch-size: {}'.format(params["batchsize"])+"\n")
    f.write('# epoch: {}'.format(params["epoch"])+"\n")
    f.write('# number of category: {}'.format(params["output-dimensions"])+"\n")
    f.write('# embedding dimension: {}'.format(params["embedding-dimensions"])+"\n")
    f.write('# current layer: {}'.format(params["currentDepth"])+"\n")
    f.write('# model-type: {}'.format(params["model-type"])+"\n")
    f.write("\n")
    f.close()

    embeddingWeights = params["embeddingWeights"]
    embeddingDimensions = params["embedding-dimensions"]
    inputData = params["inputData"]
    x_train = inputData['X_trn']
    x_test = inputData['X_val']
    y_train = inputData['Y_trn']
    y_test = inputData['Y_val']
                
    cnn_params = {"cudnn":USE_CUDNN, 
                "out_channels":params["outchannels"],
                "row_dim":embeddingDimensions, 
                "batch_size":params["batchsize"],
                "hidden_dim":params["unit"],
                "n_classes":params["output-dimensions"],
                "embeddingWeights":embeddingWeights,
                }
    if params["fineTuning"] == 0:
        cnn_params['mode'] = 'scratch'
    elif params["fineTuning"] == 1:
        cnn_params['mode'] = 'fine-tuning'
        cnn_params['load_param_node_name'] = params['upperDepth']
        
    if params["model-type"] == "XML-CNN":
        model = xml_cnn_model.CNN(**cnn_params)
    else:
        model = cnn_model.CNN(**cnn_params)

    if params["gpu"] >= 0:
        chainer.cuda.get_device_from_id(params["gpu"]).use()
        model.to_gpu()
    
    optimizer = chainer.optimizers.Adam()
    optimizer.setup(model)

    train = tuple_dataset.TupleDataset(x_train, y_train)
    test = tuple_dataset.TupleDataset(x_test, y_test)
    
    train_iter = chainer.iterators.SerialIterator(train, params["batchsize"], repeat=True, shuffle=False)
    test_iter = chainer.iterators.SerialIterator(test, params["batchsize"], repeat = False, shuffle=False)
    
    stop_trigger = training.triggers.EarlyStoppingTrigger(
    monitor='validation/main/loss',
    max_trigger=(params["epoch"], 'epoch'))


    from MyUpdater import MyUpdater
    updater = MyUpdater(train_iter, optimizer, params["output-dimensions"], device=params["gpu"])
    trainer = training.Trainer(updater, stop_trigger, out='./CNN/')
    
    from MyEvaluator import MyEvaluator
    trainer.extend(MyEvaluator(test_iter, model, class_dim=params["output-dimensions"], device=params["gpu"]))
    trainer.extend(extensions.dump_graph('main/loss'))

    trainer.extend(extensions.snapshot_object(model, 'parameters_for_multi_label_model_' + params["currentDepth"] + '.npz'),trigger=training.triggers.MinValueTrigger('validation/main/loss',trigger=(1,'epoch')))

    trainer.extend(extensions.LogReport(log_name='LOG/log_' + params["currentDepth"] + ".txt", trigger=(1, 'epoch')))

    trainer.extend(extensions.PrintReport(
        ['epoch', 'main/loss', 'validation/main/loss',
         'elapsed_time']))    
    trainer.extend(extensions.ProgressBar())

    trainer.extend(
    extensions.PlotReport(['main/loss', 'validation/main/loss'],
                          'epoch', file_name='LOG/loss_' + params["currentDepth"] + '.png'))
    
    trainer.run()


    filename = 'parameters_for_multi_label_model_' + params["currentDepth"] + '.npz'
    src = './CNN/'
    dst = './CNN/PARAMS'
    shutil.move(os.path.join(src, filename), os.path.join(dst, filename))

    print ("-"*50)
    print ("Testing...")
    
    X_tst = inputData['X_tst']
    Y_tst = inputData['Y_tst']
    N_eval = len(X_tst)

    cnn_params['mode'] = 'test-predict'
    cnn_params['load_param_node_name'] = params["currentDepth"]
    
    if params["model-type"] == "XML-CNN":
        model = xml_cnn_model.CNN(**cnn_params)
    else:
        model = cnn_model.CNN(**cnn_params)

    model.to_gpu()
    output = np.zeros([N_eval,params["output-dimensions"]],dtype=np.int8)
    output_probability_file_name = "CNN/RESULT/probability_" + params["currentDepth"] + ".csv"
    with open(output_probability_file_name, 'w') as f:
        f.write(','.join(params["learning_categories"])+"\n")
 
    test_batch_size = params["batchsize"]
    with chainer.using_config('train', False), chainer.no_backprop_mode():
        for i in tqdm(six.moves.range(0, N_eval, test_batch_size),desc="Predict Test loop"):
            x = chainer.Variable(chainer.cuda.to_gpu(X_tst[i:i + test_batch_size]))
            t = Y_tst[i:i + test_batch_size]
            net_output = F.sigmoid(model(x))
            output[i: i + test_batch_size] = select_function(net_output.data)
            with open(output_probability_file_name , 'a') as f:
                tmp = chainer.cuda.to_cpu(net_output.data)
                low_values_flags = tmp < 0.001
                tmp[low_values_flags] = 0
                np.savetxt(f,tmp,fmt='%.4g',delimiter=",")
    return output

def  load_top_level_weights(params):
    print ("-"*50)
    print ("Testing...")

    embeddingWeights = params["embeddingWeights"]
    embeddingDimensions = params["embedding-dimensions"]
    inputData = params["inputData"]

    cnn_params = {"cudnn":USE_CUDNN, 
                "out_channels":params["outchannels"],
                "row_dim":embeddingDimensions, 
                "batch_size":params["batchsize"],
                "hidden_dim":params["unit"],
                "n_classes":params["output-dimensions"],
                "embeddingWeights":embeddingWeights,
                }
                   
    X_tst = inputData['X_tst']
    Y_tst = inputData['Y_tst']
    N_eval = len(X_tst)

    cnn_params['mode'] = 'test-predict'
    cnn_params['load_param_node_name'] = params["currentDepth"]
    if params["model-type"] == "XML-CNN":
        model = xml_cnn_model.CNN(**cnn_params)
    else:
        model = cnn_model.CNN(**cnn_params)

    model.to_gpu()
    output = np.zeros([N_eval,params["output-dimensions"]],dtype=np.int8)
    output_probability_file_name = "CNN/RESULT/probability_" + params["currentDepth"] + ".csv"
    with open(output_probability_file_name, 'w') as f:
        f.write(','.join(params["learning_categories"])+"\n")
        
    test_batch_size = params["batchsize"]
    with chainer.using_config('train', False), chainer.no_backprop_mode():
        for i in tqdm(six.moves.range(0, N_eval, test_batch_size),desc="Predict Test loop"):
            x = chainer.Variable(chainer.cuda.to_gpu(X_tst[i:i + params["batchsize"]]))
            t = Y_tst[i:i + test_batch_size]
            net_output = F.sigmoid(model(x))
            output[i: i + test_batch_size] = select_function(net_output.data)
            with open(output_probability_file_name , 'a') as f:
                tmp = chainer.cuda.to_cpu(net_output.data)
                low_values_flags = tmp < 0.001
                tmp[low_values_flags] = 0
                np.savetxt(f,tmp,fmt='%.4g',delimiter=",")
    return output


