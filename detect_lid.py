import argparse
from common.util import *
from setup_paths import *
from sklearn.preprocessing import scale, MinMaxScaler, StandardScaler
from lid.util import (random_split, block_split, train_lr, compute_roc, get_lids_random_batch, get_noisy_samples)

#method from the original paper gitub code available on /lid folder
def get_lid(model, X_test, X_test_noisy, X_test_adv, k=10, batch_size=100, dataset='mnist'):
    """
    Get local intrinsic dimensionality
    :param model: 
    :param X_test: 
    :param X_test_noisy: 
    :param X_test_adv: 
    :return: artifacts: positive and negative examples with lid values, 
            labels: adversarial (label: 1) and normal/noisy (label: 0) examples
    """
    print('Extract local intrinsic dimensionality: k = %s' % k)
    lids_normal, lids_noisy, lids_adv = get_lids_random_batch(model, X_test, X_test_noisy, X_test_adv, dataset, k, batch_size)
    print("lids_normal:", lids_normal.shape)
    print("lids_noisy:", lids_noisy.shape)
    print("lids_adv:", lids_adv.shape)

    lids_pos = lids_adv
    lids_neg = np.concatenate((lids_normal, lids_noisy))
    artifacts, labels = merge_and_generate_labels(lids_pos, lids_neg)

    return artifacts, labels

def main(args):
    set_seed(args)

    assert args.dataset in DATASETS, \
        "Dataset parameter must be either {}".format(DATASETS)
    ATTACKS = ATTACK[DATASETS.index(args.dataset)]
    assert args.attack in ATTACKS, \
        "Train attack must be either {}".format(ATTACKS)
    if args.dataset != 'imagenet':
        assert os.path.isfile('{}cnn_{}.pt'.format(checkpoints_dir, args.dataset)), \
            'model file not found... must first train model'
    assert os.path.isfile('{}{}_{}.npy'.format(adv_data_dir, args.dataset, args.attack)), \
        'adversarial sample file not found... must first craft adversarial samples'

    #------generate characteristics
    print('Loading the data and model...')
    # Load the model
    if args.dataset == 'mnist':
        from baseline.cnn.cnn_mnist import MNISTCNN as myModel
        model_class = myModel(mode='load', filename='cnn_{}.pt'.format(args.dataset))
        classifier = model_class.classifier
    elif args.dataset == 'cifar':
        from baseline.cnn.cnn_cifar10 import CIFAR10CNN as myModel
        model_class = myModel(mode='load', filename='cnn_{}.pt'.format(args.dataset))
        classifier = model_class.classifier
    elif args.dataset == 'imagenet':
        from baseline.cnn.cnn_imagenet import ImageNetCNN as myModel
        model_class = myModel(filename='cnn_{}.pt'.format(args.dataset))
        classifier = model_class.classifier
    elif args.dataset == 'svhn':
        from baseline.cnn.cnn_svhn import SVHNCNN as myModel
        model_class = myModel(mode='load', filename='cnn_{}.pt'.format(args.dataset))
        classifier = model_class.classifier
        
    # Load the dataset
    X_test, Y_test = model_class.x_test, model_class.y_test

    # Check attack type, select adversarial and noisy samples accordingly
    print('Loading noisy and adversarial samples...')
    # Load adversarial samples
    X_test_adv = np.load('{}{}_{}.npy'.format(adv_data_dir, args.dataset, args.attack))
    print("X_test_adv: ", X_test_adv.shape)

    # as there are some parameters to tune for noisy example, so put the generation
    # step here instead of the adversarial step which can take many hours
    print('Crafting %s noisy samples. ' % args.dataset)
    X_test_noisy = get_noisy_samples(X_test, X_test_adv, args.dataset, args.attack)

    # Refine the normal, noisy and adversarial sets to only include samples for
    # which the original version was correctly classified by the model
    preds_test = classifier.predict(X_test)
    inds_correct = np.where(preds_test.argmax(axis=1) == Y_test.argmax(axis=1))[0]
    print("Number of correctly predict images: %s" % (len(inds_correct)))

    X_test = X_test[inds_correct]
    X_test_noisy = X_test_noisy[inds_correct]
    X_test_adv = X_test_adv[inds_correct]
    Y_test = Y_test[inds_correct]
    print("X_test: ", X_test.shape)
    print("X_test_noisy: ", X_test_noisy.shape)
    print("X_test_adv: ", X_test_adv.shape)

    # extract local intrinsic dimensionality --- load if it existed
    lid_file_X = '{}{}_{}_lid_X.npy'.format(lid_results_dir, args.dataset, args.attack)
    lid_file_Y = '{}{}_{}_lid_Y.npy'.format(lid_results_dir, args.dataset, args.attack)
    if os.path.isfile(lid_file_X) & os.path.isfile(lid_file_Y):
        X = np.load(lid_file_X)
        Y = np.load(lid_file_Y)
    else:
        X, Y = get_lid(classifier.model, X_test, X_test_noisy, X_test_adv, k_lid[DATASETS.index(args.dataset)], args.batch_size, args.dataset)
        np.save(lid_file_X, X)
        np.save(lid_file_Y, Y)
    
    scaler = MinMaxScaler().fit(X)
    X = scaler.transform(X) # standarization

    print("LID: [characteristic shape: ", X.shape, ", label shape: ", Y.shape)
    # test attack is the same as training attack
    x_train, y_train, x_test, y_test = block_split(X, Y)
    print("Train data size: ", x_train.shape)
    print("Test data size: ", x_test.shape)

    ## Build detector
    print("LR Detector on [dataset: %s, train_attack: %s] with:" % (args.dataset, args.attack))
    lr = train_lr(x_train, y_train)
    
    
    #Split
    n_samples = int(len(x_test)/3)
    x_normal=x_test[:n_samples]
    x_noise=x_test[n_samples:n_samples*2]
    x_adv=x_test[n_samples*2:]
    x_test = np.concatenate([x_normal, x_adv])
    y_normal=y_test[:n_samples]
    y_noise=y_test[n_samples:n_samples*2]
    y_adv=y_test[n_samples*2:]
    y_test = np.concatenate([y_normal, y_adv])
    ind_adv_start = int(len(X_test_adv)*0.007)*100
    pred_adv = classifier.predict(X_test_adv[ind_adv_start:])
    acc_suc = np.sum(np.argmax(pred_adv, axis=1) == np.argmax(Y_test[ind_adv_start:], axis=1)) / len(Y_test[ind_adv_start:])

    inds_success = np.where(pred_adv.argmax(axis=1) != Y_test[ind_adv_start:].argmax(axis=1))[0]
    inds_fail = np.where(pred_adv.argmax(axis=1) == Y_test[ind_adv_start:].argmax(axis=1))[0]
    X_success = np.concatenate([x_normal[inds_success], x_adv[inds_success]])
    Y_success = np.concatenate([np.zeros(len(inds_success), dtype=bool), np.ones(len(inds_success), dtype=bool)])
    X_fail = np.concatenate([x_normal[inds_fail], x_adv[inds_fail]])
    Y_fail = np.concatenate([np.zeros(len(inds_fail), dtype=bool), np.ones(len(inds_fail), dtype=bool)])
    # X_success = np.concatenate([x_noise[inds_success], x_normal[inds_success], x_adv[inds_success]])
    # Y_success = np.concatenate([np.zeros(2*len(inds_success), dtype=bool), np.ones(len(inds_success), dtype=bool)])
    # X_fail = np.concatenate([x_noise[inds_fail], x_normal[inds_fail], x_adv[inds_fail]])
    # Y_fail = np.concatenate([np.zeros(2*len(inds_fail), dtype=bool), np.ones(len(inds_fail), dtype=bool)])

    ## Evaluate detector on adversarial attack
    y_pred = lr.predict_proba(x_test)[:, 1]
    y_label_pred = lr.predict(x_test)

    results_all = []
    #for Y_all
    acc_all, tpr_all, fpr_all, tp_all, ap_all, fb_all, an_all = evalulate_detection_test(y_test[:][:,0], y_label_pred)
    fprs_all, tprs_all, thresholds_all = roc_curve(y_test[:][:,0], y_pred)
    roc_auc_all = auc(fprs_all, tprs_all)
    print("AUC: {:.4f}%, Overall accuracy: {:.4f}%, FPR value: {:.4f}%".format(100*roc_auc_all, 100*acc_all, 100*fpr_all))

    curr_result = {'type':'all', 'nsamples': len(inds_correct),	'acc_suc': acc_suc,	\
                    'acc': acc_all, 'tpr': tpr_all, 'fpr': fpr_all, 'tp': tp_all, 'ap': ap_all, 'fb': fb_all, 'an': an_all,	\
                    'tprs': list(fprs_all), 'fprs': list(tprs_all),	'auc': roc_auc_all}
    results_all.append(curr_result)

    #for Y_success
    if len(inds_success)==0:
        tpr_success=np.nan
        curr_result = {'type':'success', 'nsamples': 0,	'acc_suc': 0,	\
                    'acc': np.nan, 'tpr': np.nan, 'fpr': np.nan, 'tp': np.nan, 'ap': np.nan, 'fb': np.nan, 'an': np.nan,	\
                    'tprs': np.nan, 'fprs': np.nan,	'auc': np.nan}
        results_all.append(curr_result)
    else:
        Y_success_pred_score = lr.predict_proba(X_success)[:, 1]
        Y_success_pred = lr.predict(X_success)
        accuracy_success, tpr_success, fpr_success, tp_success, ap_success, fb_success, an_success = evalulate_detection_test(Y_success, Y_success_pred)
        fprs_success, tprs_success, thresholds_success = roc_curve(Y_success, Y_success_pred_score)
        roc_auc_success = auc(fprs_success, tprs_success)

        curr_result = {'type':'success', 'nsamples': len(inds_success),	'acc_suc': 0,	\
                    'acc': accuracy_success, 'tpr': tpr_success, 'fpr': fpr_success, 'tp': tp_success, 'ap': ap_success, 'fb': fb_success, 'an': an_success,	\
                    'tprs': list(fprs_success), 'fprs': list(tprs_success),	'auc': roc_auc_success}
        results_all.append(curr_result)

    #for Y_fail
    if len(inds_fail)==0:
        tpr_fail=np.nan
        curr_result = {'type':'fail', 'nsamples': 0,	'acc_suc': 0,	\
                    'acc': np.nan, 'tpr': np.nan, 'fpr': np.nan, 'tp': np.nan, 'ap': np.nan, 'fb': np.nan, 'an': np.nan,	\
                    'tprs': np.nan, 'fprs': np.nan,	'auc': np.nan}
        results_all.append(curr_result)
    else:
        Y_fail_pred_score = lr.predict_proba(X_fail)[:, 1]
        Y_fail_pred = lr.predict(X_fail)
        accuracy_fail, tpr_fail, fpr_fail, tp_fail, ap_fail, fb_fail, an_fail = evalulate_detection_test(Y_fail, Y_fail_pred)
        fprs_fail, tprs_fail, thresholds_fail = roc_curve(Y_fail, Y_fail_pred_score)
        roc_auc_fail = auc(fprs_fail, tprs_fail)

        curr_result = {'type':'fail', 'nsamples': len(inds_fail),	'acc_suc': 0,	\
                    'acc': accuracy_fail, 'tpr': tpr_fail, 'fpr': fpr_fail, 'tp': tp_fail, 'ap': ap_fail, 'fb': fb_fail, 'an': an_fail,	\
                    'tprs': list(fprs_fail), 'fprs': list(tprs_fail),	'auc': roc_auc_fail}
        results_all.append(curr_result)
    
    print('{:>15} attack - accuracy of pretrained model: {:7.2f}% \
        - detection rates ------ SAEs: {:7.2f}%, FAEs: {:7.2f}%'.format(args.attack, 100*acc_suc, 100*tpr_success, 100*tpr_fail))

    import csv
    with open('{}{}_{}.csv'.format(lid_results_dir, args.dataset, args.attack), 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in results_all:
            writer.writerow(row)
            
    print('Done!')

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--dataset', help="Dataset to use; either {}".format(DATASETS), required=True, type=str)
    parser.add_argument('-a', '--attack', help="Attack to use train the discriminator; either  {}".format(ATTACK), required=True, type=str)
    parser.add_argument('-b', '--batch_size', help="The batch size to use for training.", default=100, type=int)
    parser.add_argument('-k', '--k_nearest', help="The number of nearest neighbours to use; either 10, 20, 100 ", required=False, type=int)
    parser.add_argument('-s', '--seed', help='set seed for model', default=123, type=int)
    args = parser.parse_args()
    main(args)
