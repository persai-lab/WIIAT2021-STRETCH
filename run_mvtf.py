import copy
import os
import pickle
import logging
from easydict import EasyDict
from mvtf.utils import create_dirs, setup_logging
from mvtf_agent import MVTFAgent
from mvtf.utils import *
from multiprocessing import Pool, Lock

output_lock = Lock()


def get_data(mode, fold, pred_type):
    with open('data/data.pkl', 'rb') as f:
        data = pickle.load(f)
    activity = data["activity"]
    user_records_size = data["user_records_size"]
    n_users = data["num_users"]
    n_attempts = data['num_time_index'] + 1
    n_items = data["num_items"]

    train_users = data["fold_{}".format(fold)]["train"]
    val_users = data["fold_{}".format(fold)]["val"]
    if mode == "test":
        train_users += val_users
        test_users = data["fold_{}".format(fold)]["test"]
    else:
        test_users = val_users

    stress_train_data = []
    rate_train_data = []
    done_train_data = []
    for user in train_users:
        for (user, time_index, resource, item, value) in activity[user]:
            # if resource == "rate":
            #     continue

            if resource == "stress":
                stress_train_data.append([user, time_index, resource, item, value])
            elif resource == "rate":
                rate_train_data.append([user, time_index, resource, item, value])
            elif resource == "done":
                done_train_data.append([user, time_index, resource, item, value])
            else:
                raise ValueError

    test_data = []
    max_test_index = 0
    min_test_index = float('inf')
    for user in test_users:
        pred_record_size = user_records_size[user][pred_type]
        start_test_index = pred_record_size / 2
        count = 0
        for (user, time_index, resource, item, value) in sorted(activity[user], key=lambda x: x[1]):
            # if resource == "rate":
            #     continue

            if count < start_test_index:
                if resource == "stress":
                    stress_train_data.append([user, time_index, resource, item, value])
                elif resource == "rate":
                    rate_train_data.append([user, time_index, resource, item, value])
                elif resource == "done":
                    done_train_data.append([user, time_index, resource, item, value])
                else:
                    raise ValueError
            else:
                test_data.append([user, time_index, resource, item, value])
                if time_index > max_test_index and resource == pred_type:
                    max_test_index = time_index
                if time_index < min_test_index and resource == pred_type:
                    min_test_index = time_index
            if resource == pred_type:
                count += 1
    return n_users, n_attempts, n_items, stress_train_data, rate_train_data, done_train_data, \
           test_data, int(min_test_index), int(max_test_index)


def process_config(config_dict):
    config = EasyDict(config_dict)
    config.summary_dir = os.path.join("experiments", config.agent, "summaries/")
    config.checkpoint_dir = os.path.join("experiments", config.agent, "checkpoints/")
    config.out_dir = os.path.join("experiments", config.agent, "out/")
    config.log_dir = os.path.join("experiments", config.agent, "logs/")
    create_dirs([config.summary_dir, config.checkpoint_dir, config.out_dir, config.log_dir])

    # setup logging in the project
    setup_logging(config.log_dir)

    logging.getLogger().info("Hi, This is root.")
    logging.getLogger().info("After the configurations are successfully processed "
                             "and dirs are created.")
    logging.getLogger().info(config)
    logging.getLogger().info("The pipeline of the project will begin now.")
    return config


def single_run(mode, config_dict, pred_type):
    metric1_list = []
    metric2_list = []
    count_list = []
    for fold in range(1, 6):
        all_data = get_data(mode, fold, pred_type)
        n_users, n_attempts, n_items = all_data[0: 3]
        stress_train_data, rate_train_data, done_train_data, test_data = all_data[3: 7]
        first_index, last_index = all_data[7: 9]
        config = process_config(config_dict)
        config.checkpoint = "checkpoint_fold_{}.pth.tar".format(fold)
        config.output = "{}_output_fold_{}.pth.tar".format(pred_type, fold)
        config.pred_type = pred_type
        config.n_users = n_users
        config.n_attempts = n_attempts
        config.n_items = n_items
        config.stress_train_data = stress_train_data
        config.rate_train_data = rate_train_data
        config.done_train_data = done_train_data
        config.test_data = test_data
        config.min_test_index = first_index
        config.max_test_index = last_index
        agent = MVTFAgent(config)
        count, metric1, metric2 = agent.run()
        metric1_list.append(metric1)
        metric2_list.append(metric2)
        count_list.append(count)
    if mode == "val":
        output_lock.acquire()
        with open("cv_mvtf_{}_user_time_factor_clipping_output.csv".format(pred_type), "a") as f:
            f.write("{},{:.5f},{:.5f},{:.5f},{:.5f},{:.5f},{:.5f},{:.5f}\n".format(
                pred_type, config.n_factors, config.learning_rate, config.bias_learning_rate,
                config.weight_decay, config.min_epoch, config.smooth_weight, np.mean(metric1_list)))
        output_lock.release()
    else:
        print("{},{:.5f},{:.5f},{:.5f},{:.5f},{:.5f},{:.5f},{:.5f}\n".format(
            pred_type, config.n_factors, config.learning_rate, config.bias_learning_rate,
            config.weight_decay, config.min_epoch, config.smooth_weight, np.mean(metric1_list)))
        for count, metric1, metric2 in zip(count_list, metric1_list, metric2_list):
            print(metric1, metric2)


def tune(pred_type, clipping, num_cpus):
    para_list = []
    for n_factors in [5]:  # 1 to 5
        for lr in [0.1, 0.01, 0.001, 0.0001, 0.00001]:
            for bias_lr in [0.1, 0.01]:
                for weight_decay in [0., 0.5, 1., 2., 10., 20.]:
                    for min_epoch in range(1, 5):
                        for smooth_weight in [0, 0.001, 0.01, 0.1, 0.5, 1.]:
                            config_dict = {
                                "agent": "MVTFAgent",
                                "mode": "val",
                                "metric": "rmse",
                                "cuda": False,
                                "seed": 1024,
                                "n_factors": n_factors,
                                "optimizer": "sgd",
                                "learning_rate": lr,
                                "bias_learning_rate": bias_lr,
                                "clipping": clipping,
                                "epsilon": 0.1,
                                "weight_decay": weight_decay,
                                "max_grad_norm": 50.0,
                                "min_epoch": min_epoch,
                                "smooth_weight": smooth_weight,
                                "max_epoch": 10,
                                "log_interval": 10,
                                "validate_every": 1,
                                "save_checkpoint": False,
                                "checkpoint_file": "checkpoint.pth.tar"
                            }
                            config_d = copy.deepcopy(config_dict)
                            para = (mode, config_d, pred_type)
                            para_list.append(para)
                            # single_run(*para)
    pool = Pool(processes=num_cpus)
    pool.starmap(single_run, para_list)
    pool.close()


if __name__ == '__main__':
    pred_type = "stress"
    # pred_type = "rate"
    # pred_type = "done"
    mode = "test"
    # mode = "val"

    # clipping = ["all"]
    # clipping = ["user", "time", "item"]
    # clipping = ["user", "item"]
    # clipping = ["user", "time"]
    # clipping = ["time", "item"]
    # clipping = ["user"]
    # clipping = ["item"]
    clipping = []

    if mode == "val":
        tune(pred_type, clipping, num_cpus=20)
    else:
        if pred_type == "rate":
            config_dict = {
                "agent": "MVTFAgent",
                "mode": mode,
                "metric": "rmse",
                "cuda": False,
                "seed": 1024,
                "n_factors": 1,
                "optimizer": "sgd",
                "learning_rate": 0.1,
                "bias_learning_rate": 0.1,
                "weight_decay": 0.,
                "min_epoch": 4,
                "smooth_weight": 0.1,
                "clipping": ["item"],
                "epsilon": 0.1,
                "max_grad_norm": 50.0,
                "max_epoch": 10,
                "log_interval": 10,
                "validate_every": 1,
                "save_checkpoint": True,
                "checkpoint_file": "checkpoint.pth.tar"
            }
        elif pred_type == "stress":
            config_dict = {
                "agent": "MVTFAgent",
                "mode": mode,
                "metric": "rmse",
                "cuda": False,
                "seed": 1024,
                "n_factors": 4,
                "optimizer": "sgd",
                "learning_rate": 0.01,
                "bias_learning_rate": 0.01,
                "weight_decay": 0.,
                "min_epoch": 4,
                "smooth_weight": 0.1,
                "clipping": [],
                "epsilon": 0.1,
                "max_grad_norm": 50.0,
                "max_epoch": 10,
                "log_interval": 10,
                "validate_every": 1,
                "save_checkpoint": False,
                "checkpoint_file": "checkpoint.pth.tar"
            }
        elif pred_type == "done":
            config_dict = {
                "agent": "MFAgent",
                "mode": mode,
                "metric": "rmse",
                "cuda": False,
                "seed": 1024,
                "n_factors": 2,
                "optimizer": "sgd",
                "learning_rate": 0.1,
                "bias_learning_rate": 0.1,
                "weight_decay": 0.,
                "min_epoch": 4,
                "smooth_weight": 0.5,
                "clipping": clipping,
                "epsilon": 0.1,
                "max_grad_norm": 50.0,
                "max_epoch": 10,
                "log_interval": 10,
                "validate_every": 1,
                "save_checkpoint": False,
                "checkpoint_file": "checkpoint.pth.tar"
            }
        single_run(mode, config_dict, pred_type)
