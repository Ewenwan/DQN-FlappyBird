import sys
sys.path.append("game/")
import wrapped_flappy_bird as game
from BrainDQN import *
import shutil
import numpy as np
import random
import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable

import PIL.Image as Image

IMAGE_SIZE = (72, 128)

def preprocess(frame):
    """Do preprocessing: resize and binarize.

       Downsampling to 128x72 size and convert to grayscale
       frame -- input frame, rgb image with 512x288 size
    """
    im = Image.fromarray(frame).resize(IMAGE_SIZE).convert(mode='L')
    out = np.asarray(im).astype(np.float32)
    out[out <= 1.] = 0.0
    out[out > 1.] = 1.0
    return out

def save_checkpoint(state, is_best, filename='checkpoint.pth.tar'):
    """Save checkpoint model to disk

        state -- checkpoint state: model weight and other info
                 binding by user
        is_best -- if the checkpoint is the best. If it is, then
                   save as a best model
    """
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, 'model_best.pth.tar')

def load__checkpoint(filename, model):
    """Load previous checkpoint model

       filename -- model file name
       model -- DQN model
    """
    checkpoint = torch.load(filename)
    print 'episode = {}'.format(checkpoint['episode'])
    model.load_state_dict(checkpoint['state_dict'])
    return model

def train_dqn(model, options, resume):
    """Train DQN

       model -- DQN model
       lr -- learning rate
       max_episode -- maximum episode
       resume -- resume previous model
       model_name -- checkpoint file name
    """
    if resume:
        if options.weight is None:
            print 'when resume, you should give weight file name.'
            return
        print 'load previous model weight: {}'.format(options.weight)
        load__checkpoint(options.weight, model)

    flappyBird = game.GameState()
    optimizer = optim.RMSprop(model.parameters(), lr=options.lr)
    ceriterion = nn.MSELoss()

    action = [1, 0]
    o, r, terminal = flappyBird.frame_step(action)
    o = preprocess(o)
    model.set_initial_state()

    if options.cuda:
        model = model.cuda()
    # in the first `OBSERVE` time steos, we dont train the model
    for i in xrange(options.observation):
        action = model.get_action_randomly()
        o, r, terminal = flappyBird.frame_step(action)
        o = preprocess(o)
        model.store_transition(o, action, r, terminal)
    # start training
    best_time_step = 0.
    for episode in xrange(options.max_episode):
        model.timeStep = 0
        model.set_train()
        total_reward = 0.
        # begin an episode!
        while True:
            optimizer.zero_grad()
            action = model.get_action()
            o_next, r, terminal = flappyBird.frame_step(action)
            total_reward += options.gamma**model.timeStep * r
            o_next = preprocess(o_next)
            model.store_transition(o_next, action, r, terminal)
            model.increase_time_step()
            # Step 1: obtain random minibatch from replay memory
            minibatch = random.sample(model.replayMemory, options.batch_size)
            state_batch = np.array([data[0] for data in minibatch])
            action_batch = np.array([data[1] for data in minibatch])
            reward_batch = np.array([data[2] for data in minibatch])
            nextState_batch = np.array([data[3] for data in minibatch])
            state_batch_var = Variable(torch.from_numpy(state_batch))
            nextState_batch_var = Variable(torch.from_numpy(nextState_batch),
                                           volatile=True)
            if options.cuda:
                state_batch_var = state_batch_var.cuda()
                nextState_batch_var = nextState_batch_var.cuda()
            # Step 2: calculate y
            q_value_next = model.forward(nextState_batch_var)

            q_value = model.forward(state_batch_var)

            y = reward_batch.astype(np.float32)
            max_q, _ = torch.max(q_value_next, dim=1)

            for i in xrange(options.batch_size):
                if not minibatch[i][4]:
                    y[i] += options.gamma*max_q.data[i][0]

            y = Variable(torch.from_numpy(y))
            action_batch_var = Variable(torch.from_numpy(action_batch))
            if options.cuda:
                y = y.cuda()
                action_batch_var = action_batch_var.cuda()
            q_value = torch.sum(torch.mul(action_batch_var, q_value), dim=1)

            loss = ceriterion(q_value, y)
            loss.backward()

            optimizer.step()
            # when the bird dies, the episode ends
            if terminal:
                break

        print 'episode: {}, epsilon: {:.4f}, max time step: {}, total reward: {:.6f}'.format(
                episode, model.epsilon, model.timeStep, total_reward)

        if model.epsilon > options.final_e:
            delta = (options.init_e - options.final_e)/options.exploration
            model.epsilon -= delta

        if episode % 100 == 0:
            ave_time = test_dqn(model, episode)

        if ave_time > best_time_step:
            best_time_step = ave_time
            save_checkpoint({
                'episode': episode,
                'epsilon': model.epsilon,
                'state_dict': model.state_dict(),
                'best_time_step': best_time_step,
                 }, True, 'checkpoint-episode-%d.pth.tar' %episode)
        elif episode % options.save_checkpoint_freq == 0:
            save_checkpoint({
                'episode:': episode,
                'epsilon': model.epsilon,
                'state_dict': model.state_dict(),
                'time_step': ave_time,
                 }, True, 'checkpoint-episode-%d.pth.tar' %episode)
        else:
            continue
        print 'save checkpoint, episode={}, ave time step={:.2f}'.format(
                 episode, ave_time)

def test_dqn(model, episode):
    """Test the behavor of dqn when training

       model -- dqn model
       episode -- current training episode
    """
    model.set_eval()
    ave_time = 0.
    for test_case in xrange(5):
        model.timeStep = 0
        flappyBird = game.GameState()
        o, r, terminal = flappyBird.frame_step([1, 0])
        o = preprocess(o)
        model.set_initial_state()
        while True:
            action = model.get_optim_action()
            o, r, terminal = flappyBird.frame_step(action)
            if terminal:
                break
            o = preprocess(o)
            model.currentState = np.append(model.currentState[1:,:,:], o.reshape((1,)+o.shape), axis=0)
            model.increase_time_step()
        ave_time += model.timeStep
    ave_time /= 5
    print 'testing: episode: {}, average time: {}'.format(episode, ave_time)
    return ave_time


def play_game(model_file_name, cuda=False, best=True):
    """Play flappy bird with pretrained dqn model

       weight -- model file name containing weight of dqn
       best -- if the model is best or not
    """
    print 'load model file: ' + model_file_name
    checkpoint = torch.load(model_file_name)
    episode = checkpoint['episode']
    print 'pretrained episode = {}'.format(episode)
    if best:
        time_step = checkpoint['best_time_step']
        print 'best time step is {}'.format(time_step)
    else:
        time_step = checkpoint['time_step']
        print 'time step is {}'.format(time_step)
    epsilon = checkpoint['epsilon']
    print 'epsilon = {:.5f}'.format(epsilon)
    model = BrainDQN(epsilon=epsilon, mem_size=0, cuda=cuda)
    model.load_state_dict(checkpoint['state_dict'])

    model.set_eval()
    bird_game = game.GameState()
    model.set_initial_state()
    if cuda:
        model = model.cuda()
    while True:
        action = model.get_optim_action()
        o, r, terminal = bird_game.frame_step(action)
        if terminal:
            break
        o = preprocess(o)

        model.currentState = np.append(model.currentState[1:,:,:], o.reshape((1,)+o.shape), axis=0)

        model.increase_time_step()
    print 'total time step is {}'.format(model.timeStep)
