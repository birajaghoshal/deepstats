import scipy.stats
import numpy as np
import dataloader
from keras.models import Sequential
from keras.layers import Activation, Dense, Dropout, ELU
from layers import Bayesian, PoorBayesian, ProbabilisticDropout, BayesianConvolution2D
from objectives import bayesian_loss
import time
import pandas as pd
from sklearn import metrics

def create_model(network_model, batch_size, in_dim, out_dim, nb_batchs):
    mean_prior = 0.0
    std_prior = 0.05
    model = Sequential()
    if network_model == 'mlp-bayesian':
        model.add(Bayesian(hidden_layers[0], mean_prior, std_prior, batch_input_shape=[batch_size, in_dim]))
        model.add(ELU())
        for h in hidden_layers[1:]:
            model.add(Bayesian(h, mean_prior, std_prior))
            model.add(ELU())
        model.add(Bayesian(out_dim, mean_prior, std_prior))
        model.add(Activation('softmax'))
        loss = bayesian_loss(model, mean_prior, std_prior, batch_size, nb_batchs)

    elif network_model == 'convolutional-poor-bayesian':
        model.add(BayesianConvolution2D(hidden_layers[0], mean_prior, std_prior, batch_input_shape=[batch_size, in_dim]))
        model.add(ELU())
        for h in hidden_layers[1:]:
            model.add(Bayesian(h, mean_prior, std_prior))
            model.add(ELU())
        model.add(Bayesian(out_dim, mean_prior, std_prior))
        model.add(Activation('softmax'))
        loss = bayesian_loss(model, mean_prior, std_prior, batch_size, nb_batchs)

    elif network_model == 'poor-bayesian':
        model.add(PoorBayesian(hidden_layers[0], mean_prior, std_prior, input_shape=[in_dim]))
        model.add(ELU())
        for h in hidden_layers[1:]:
            model.add(PoorBayesian(h, mean_prior, std_prior))
            model.add(ELU())
        model.add(PoorBayesian(out_dim, mean_prior, std_prior))
        model.add(Activation('softmax'))
        loss = bayesian_loss(model, mean_prior, std_prior, batch_size, nb_batchs)

    elif network_model == 'mlp-dropout':
        model.add(Dense(hidden_layers[0], input_shape=[in_dim]))
        model.add(ELU())
        for h in hidden_layers[1:]:
            model.add(Dense(h))
            model.add(ELU())
            model.add(ProbabilisticDropout(dropout_p))
        model.add(Dense(out_dim))
        model.add(Activation('softmax'))
        loss = 'categorical_crossentropy'

    elif network_model == 'mlp-deterministic':
        model.add(Dense(hidden_layers[0], input_shape=[in_dim]))
        model.add(ELU())
        for h in hidden_layers[1:]:
            model.add(Dense(h))
            model.add(ELU())
            model.add(Dropout(dropout_p))
        model.add(Dense(out_dim))
        model.add(Activation('softmax'))
        loss = 'categorical_crossentropy'

    model.compile(loss=loss, optimizer='adam', metrics=['accuracy'])

def model_test(model, batch_size, X_test, y_test, labels_to_test):
    cnt = 0
    acc = 0
    for x, y in zip(X_test, y_test):
        if y in labels_to_test:
            cnt += 1
            probs = model.predict(np.array([x]*batch_size), batch_size=batch_size)
            pred_mean = probs.mean(axis=0)
            o = np.argmax(pred_mean)
            if o == labels_to_test.index(y):
                acc += 1
    return acc/cnt


def anomaly(experiment_name, network_model, dataset, inside_labels, unknown_labels, with_unknown,
            batch_size=100,
            nb_epochs=100,
            hidden_layers=[512, 512],
            acc_threshold=0.99,
            dropout_p=0.5,
            save_weights=False):

    print('#'*50)
    print('Experiment:', experiment_name)
    print('model:', network_model)
    print('dataset:', dataset)
    print('inside_labels:', str(inside_labels))
    print('unknown_labels:', str(unknown_labels))
    print('batch_size:', batch_size)
    print('nb_epochs:', nb_epochs)
    print('-'*50)

    inside_labels.sort()
    unknown_labels.sort()

    (X_train, y_train), (X_test, y_test) = dataloader.load(dataset,
                                                           inside_labels,
                                                           unknown_labels,
                                                           with_unknown)

    in_dim = X_train.shape[1]
    out_dim = y_train.shape[1]
    nb_batchs = X_train.shape[0]//batch_size


    model = create_model(network_model, batch_size, in_dim, out_dim, nb_batchs)

    if network_model.endswith('bayesian') and 'poor' not in network_model:
        mod = X_train.shape[0]%batch_size
        if mod:
            X_train = X_train[:-mod]
            y_train = y_train[:-mod]

    start_time = time.time()
    model.fit(X_train, y_train, nb_epoch=nb_epochs, batch_size=batch_size)
    end_time = time.time()

    if save_weights and with_unknown:
        model.save_weights('weights/'+dataset+'-with-unknown/'+experiment_name+'.h5', overwrite=True)
    elif save_weights:
        model.save_weights('weights/'+dataset+'-without-unknown/'+experiment_name+'.h5', overwrite=True)

    test_pred_std = {x:[] for x in range(10)}
    test_entropy = {x:[] for x in range(10)}

    cnt_in = 0
    acc_in = 0
    for i, (x, y) in enumerate(zip(X_test, y_test)):
        if 'poor-bayesian' in network_model:
            probs = model.predict(np.array([x]*batch_size), batch_size=1)
        else:
            probs = model.predict(np.array([x]*batch_size), batch_size=batch_size)
        pred_mean = probs.mean(axis=0)
        pred_std = probs.std(axis=0)
        entropy = scipy.stats.entropy(pred_mean)

        test_pred_std[y].append(pred_std.mean())
        test_entropy[y].append(entropy)

        if y in inside_labels:
            o = np.argmax(pred_mean)
            cnt_in += 1
            if o == inside_labels.index(y):
                acc_in += 1


    # Anomaly detection
    # by classical prediction entropy
    def anomaly_detection(anomaly_score_dict, metric_name, df):
        threshold = np.logspace(-10.0, 1.0, 1000)
        acc = {}
        for t in threshold:
            tp = 0.0
            tn = 0.0
            for l in anomaly_score_dict:
                if l in unknown_labels:
                    continue

                if l in inside_labels:
                    tp += (np.array(anomaly_score_dict[l]) < t).mean()
                else:
                    tn += (np.array(anomaly_score_dict[l]) >= t).mean()

            tp /= len(inside_labels)
            tn /= (10.0 - len(unknown_labels)) - len(inside_labels)
            bal_acc = (tp + tn)/2.0
            f1_score = 2.0*tp/(2.0 + tp - tn)
            acc[t] = [bal_acc, f1_score, tp, tn]

        trues = []
        scores = []
        for l in anomaly_score_dict:
            if l in unknown_labels: continue

            scores += anomaly_score_dict[l]
            if l in inside_labels:
                trues += [0]*len(anomaly_score_dict[l])
            else:
                trues += [1]*len(anomaly_score_dict[l])
        assert len(trues) == len(scores)

        auc = metrics.roc_auc_score(trues, scores)

        print("{}\tscore\tthreshold\tTP\tTN".format(metric_name))
        sorted_acc = sorted(acc.items(), key=lambda x: x[1][0], reverse=True)
        print("\tbalanced_acc\t{:.3f}\t{:.3f}\t\t{:.3f}\t{:.3f}".format(sorted_acc[0][1][0], sorted_acc[0][0], sorted_acc[0][1][2], sorted_acc[0][1][3]))
        df.set_value(experiment_name, metric_name + "_bal_acc", sorted_acc[0][1][0])

        sorted_acc = sorted(acc.items(), key=lambda x: x[1][1], reverse=True)
        print("\tf1_score\t{:.3f}\t{:.3f}\t\t{:.3f}\t{:.3f}".format(sorted_acc[0][1][1], sorted_acc[0][0], sorted_acc[0][1][2], sorted_acc[0][1][3]))
        df.set_value(experiment_name, metric_name + '_f1_score', sorted_acc[0][1][1])
        df.set_value(experiment_name, metric_name + "_auc", auc)
        return df

    print('-'*50)
    df = pd.DataFrame()
    df.set_value(experiment_name, "train_time", end_time - start_time)
    df.set_value(experiment_name, "dataset", dataset)
    df.set_value(experiment_name, "test_acc", acc_in/cnt_in)
    df.set_value(experiment_name, "inside_labels", str(inside_labels))
    df.set_value(experiment_name, "unknown_labels", str(unknown_labels))
    df.set_value(experiment_name, "epochs", nb_epochs)
    df = anomaly_detection(test_pred_std, "pred_std_", df)
    df = anomaly_detection(test_entropy, "entropy_", df)
    return df

#out = anomaly("test", "bayesian", "mnist",  [0, 1, 4, 8], [7, 9], with_unknown = False)
