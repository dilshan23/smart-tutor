# drqn.py
# @author: Lisa Wang
# @created: May 11 2017
#
#===============================================================================
# DESCRIPTION:
# Implements DQN model
# Given an input sequence of observations, predict the Q-value for each next action.
#
#===============================================================================
# CURRENT STATUS: In progress
#===============================================================================
# USAGE: import drqn

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import datetime
import tensorflow as tf
import tflearn
import numpy as np
import random
import utils
import dataset_utils as d_utils
import models_dict_utils
FLAGS = tf.flags.FLAGS

class DRQNModel(object):

    def __init__(self, model_id, timesteps=100, load_checkpoint=False):
        print('Loading DQN RNN model...')
        tf.reset_default_graph()
        self.timesteps = timesteps
        self.model_dict = models_dict_utils.load_model_dict(model_id)
        self.graph_ops = build_tf_graph_drqn(timesteps, self.model_dict["n_inputdim"], self.model_dict["n_hidden"], self.model_dict["n_outputdim"])
        self.experience_buffer = ExperienceBuffer()


    def predict(self, session, inputs):
        """
        predicts Q-values for the given input sequence and each action. Result is an array of length n_actions.
        :param input_data: of shape (n_samples, n_timesteps, n_inputdim).

        :return: Q-values
        """
        tf.reset_default_graph()
        q_inputs = self.graph_ops["q_inputs"]
        q_values = self.graph_ops["q_values"]

        n_samples, n_timesteps, n_inputdim = inputs.shape
        assert(n_inputdim == self.model_dict["n_inputdim"]), "input dimension of data doesn't match the model."
        if n_timesteps < self.timesteps:  # pad inputs and mask
            padded_input = np.zeros((n_samples, self.timesteps, n_inputdim))
            padded_input[:, :n_timesteps, :] = inputs[:, :, :]
            inputs = padded_input
        elif n_timesteps > self.timesteps: # truncate inputs and mask
            inputs = inputs[:, :self.timesteps, :]

        return q_values.eval(session=session, feed_dict={q_inputs: [inputs]})






def build_drqn(n_timesteps, n_inputdim, n_hidden, n_actions):
    """
    :param n_timesteps:
    :param n_inputdim:
    :param n_hidden:
    :param n_actions equivalent to number of actions
    :return:
    """
    inputs = tf.placeholder(tf.float32, [None, n_timesteps, n_inputdim])
    net, hidden_states_1 = tflearn.lstm(inputs, n_hidden, return_seq=True, return_state=True, name="lstm_1")
    q_values = tflearn.lstm(net, n_actions, return_seq=True, activation='linear', name="lstm_2")
    q_values = tf.stack(q_values, axis=1) # tensor shape(None, n_timesteps, n_actions)
    return inputs, q_values


def build_tf_graph_drqn(n_timesteps, n_inputdim, n_hidden, n_actions):
    tf.reset_default_graph()
    # Create shared deep q network
    q_inputs, q_net = build_drqn(n_timesteps, n_inputdim, n_hidden, n_actions)
    net_params = tf.trainable_variables()
    q_values = q_net
    # Define cost and gradient update op
    a = tf.placeholder("float", [None, n_timesteps, n_actions])
    y = tf.placeholder("float", [None, n_timesteps])
    action_q_values = tf.reduce_sum(tf.multiply(q_values, a), reduction_indices=2)  # shape [None, n_timesteps]
    # compute td cost as mean square error of target q and predicted q
    cost = tflearn.mean_square(action_q_values, y)
    optimizer = tf.train.AdamOptimizer(learning_rate=0.0001)
    grad_update = optimizer.minimize(cost, var_list=net_params)

    # TODO: for now we will use a single Q network. Since we use offline data, there is less of a risk to instability.

    graph_ops = {"q_inputs": q_inputs,
                 "q_values": q_values,
                 "a": a,
                 "y": y,
                 "grad_update": grad_update}
    return graph_ops



def train(drqn_model, session, saver, experience_buffer, gamma=0.99, batch_sz=16, n_epoch=16,
          load_checkpoint=False, checkpoint_interval=1000, ckpt_path=""):
    """
    Treat our offline data as the experience replay buffer and we only train on "experience"
    Data could be provided with the experience buffer (list of (s,a,r,s') tuples)
    1. experience buffer could go through data in order, in chunks of 64
    2. randomly sample a batch of 64 samples
    """

    summary_ops = build_summaries()
    summary_op = summary_ops[-1]


    graph_ops = drqn_model.graph_ops
    # unpack graph_ops
    q_inputs = graph_ops["q_inputs"]
    q_values = graph_ops["q_values"]
    a = graph_ops["a"]
    y = graph_ops["y"]
    grad_update = graph_ops["grad_update"]

    summary_placeholders, assign_ops, summary_op = summary_ops


    if load_checkpoint == True:
        print('Loading Model...')
        ckpt = tf.train.get_checkpoint_state(ckpt_path)
        saver.restore(session, ckpt.model_checkpoint_path)


    training_steps = int(n_epoch * (experience_buffer.buffer_sz / batch_sz))
    # one training step corresponds to one update to the Q network

    for t in xrange(training_steps):
        print ("Training step: {}".format(t))
        # traces is a list/batch of experience traces. Each trace is a tuple of state_action_Data and rewards.
        train_batch = experience_buffer.sample_in_order(batch_sz)

        # make sure that batches are over multiple timesteps, should be of shape (batch_sz, n_timesteps, ?)
        s_batch = stack_batch(train_batch[:,:,0]) # current states
        a_batch = stack_batch(train_batch[:,:,1]) # actions
        r_batch = stack_batch(train_batch[:,:,2]) # rewards
        sp_batch = stack_batch(train_batch[:,:,3])  # next states

        y_batch = r_batch + gamma * np.amax(q_values.eval(session=session, feed_dict={q_inputs: sp_batch}), axis=2)

        # Update the network with our target values
        session.run(grad_update, feed_dict={y: y_batch,
                                            a: a_batch,
                                            q_inputs: s_batch})

        # Save model progress
        if t % checkpoint_interval == 0:
            saver.save(session, ckpt_path, global_step=t)


def stack_batch(batch):
    stacked_batch = np.array([[np.array(batch[i, j]) for j in xrange(batch.shape[1])] for i in xrange(batch.shape[0])])
    return stacked_batch


def evaluate(session, graph_ops, saver, ckpt_path, n_timesteps, eval_buffer):
    saver.restore(session, ckpt_path)
    print("Restored model weights from ", ckpt_path)

    q_inputs = graph_ops["q_inputs"]
    q_values = graph_ops["q_values"]

    n_eval_episodes = eval_buffer.buffer_sz
    for i in xrange(n_eval_episodes):
        # get initial state
        ep_reward = 0.0
        terminal = False

        train_batch = eval_buffer.sample_in_order(batch_sz=1)

        # make sure that batches are over multiple timesteps, should be of shape (batch_sz, n_timesteps, ?)
        s_batch = stack_batch(train_batch[:, :, 0])  # current states
        a_batch = stack_batch(train_batch[:, :, 1])  # actions
        r_batch = stack_batch(train_batch[:, :, 2])  # rewards
        sp_batch = stack_batch(train_batch[:, :, 3])  # next states

        Q = q_values.eval(session=session, feed_dict={q_inputs: s_batch})




def build_summaries(scalar_summary, merge_all_summaries):
    episode_reward = tf.Variable(0.)
    scalar_summary("Reward", episode_reward)
    episode_ave_max_q = tf.Variable(0.)
    scalar_summary("Qmax Value", episode_ave_max_q)
    logged_epsilon = tf.Variable(0.)
    scalar_summary("Epsilon", logged_epsilon)
    # Threads shouldn't modify the main graph, so we use placeholders
    # to assign the value of every summary (instead of using assign method
    # in every thread, that would keep creating new ops in the graph)
    summary_vars = [episode_reward, episode_ave_max_q, logged_epsilon]
    summary_placeholders = [tf.placeholder("float")
                            for i in range(len(summary_vars))]
    assign_ops = [summary_vars[i].assign(summary_placeholders[i])
                  for i in range(len(summary_vars))]
    summary_op = merge_all_summaries()
    return summary_placeholders, assign_ops, summary_op



class ExperienceBuffer(object):
    """
    For experience replay
    based on implementation from https://github.com/awjuliani/DeepRL-Agents/blob/master/Deep-Recurrent-Q-Network.ipynb
    Extended with sample_in_order.
    """
    def __init__(self, buffer_sz=100):
        self.buffer = []
        self.buffer_sz = buffer_sz # max size of buffer
        self.cur_episode_index = 0
        self.max_episode_length = 0


    def add_episode(self, episode):
        if len(self.buffer) + 1 > self.buffer_sz:
            del self.buffer[0]
        self.buffer.append(episode)
        self.max_episode_length = max(self.max_episode_length, len(episode))
        assert (len(self.buffer) <= self.buffer_sz), "buffer too big"


    def sample(self, batch_sz, trace_length=-1):
        sampled_episodes = random.sample(self.buffer, batch_sz)
        return self._get_traces_from_episodes(sampled_episodes, trace_length)


    def sample_in_order(self, batch_sz, trace_length=-1):
        """
        This function can be used to go through all episodes in experience buffer in order, e.g. if you want to make sure
        that all episodes have been seen.
        Keeps track of the index of the next episode. If samples go over end of the buffer, it wraps around.
        :param batch_sz: number of traces to return
        :param trace_length: max length of each trace
        :return:
        """
        assert (batch_sz <= len(self.buffer)), "batch size is larger than experience buffer. "
        sampled_episodes = []
        if self.cur_episode_index + batch_sz <= len(self.buffer):
            sampled_episodes = self.buffer[self.cur_episode_index:self.cur_episode_index + batch_sz]
            self.cur_episode_index += batch_sz
        else:
            overhang = self.cur_episode_index + batch_sz - len(self.buffer) # number of samples to wrap around
            sampled_episodes = self.buffer[self.cur_episode_index:] + self.buffer[0:overhang]
            self.cur_episode_index = overhang
        return self._get_traces_from_episodes(sampled_episodes, trace_length)


    def _get_traces_from_episodes(self, sampled_episodes, trace_length=-1):
        """

        :param sampled_episodes:
        :param trace_length:  if -1, then return full episodes.
        :return:
        """
        sampled_traces = []
        if trace_length != -1:
            for episode in sampled_episodes:
            # choose a random point within the episode to start the trace
                point = np.random.randint(0, len(episode) + 1 - trace_length)
                sampled_traces.append(episode[point:point + trace_length])
        else:
            sampled_traces = sampled_episodes
        sampled_traces = np.array(sampled_traces)
        return sampled_traces


# def main():
#
#
#
# if __name__ == "__main__":
#     main()