# -*- coding: utf-8 -*-
import os
import csv
import time
import json
import datetime
import pickle as pkl
import tensorflow as tf
from tensorflow.contrib import learn

import data_helper
from rnn_classifier import rnn_clf
from cnn_classifier import cnn_clf
from clstm_classifier import clstm_clf

try:
    from sklearn.model_selection import train_test_split
except ImportError as e:
    error = "Please install scikit-learn."
    print(str(e) + ': ' + error)
    sys.exit()

# Show warnings and errors only
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

# Parameters
# =============================================================================

# Model choices
tf.flags.DEFINE_string('clf', 'cnn', "Type of classifiers. Default: cnn. You have three choices: [cnn, rnn, clstm]")

# Data parameters
tf.flags.DEFINE_string('data_file', 'data.csv', 'Data file path')
tf.flags.DEFINE_string('stop_word_file', 'stop_words_ch.txt', 'Stop word file path')
tf.flags.DEFINE_string('language', 'ch', "Language of the data file. You have two choices: [ch, en]")
tf.flags.DEFINE_integer('min_frequency', 1, 'Minimal word frequency')
tf.flags.DEFINE_integer('num_classes', 3, 'Number of classes')
tf.flags.DEFINE_integer('max_length', 0, 'Max document length')
tf.flags.DEFINE_integer('vocab_size', 0, 'Vocabulary size')
tf.flags.DEFINE_float('test_size', 0.1, 'Cross validation test size')

# Hyperparameters
tf.flags.DEFINE_integer('embedding_size', 128, 'Word embedding size')
tf.flags.DEFINE_string('filter_sizes', '3, 4, 5', 'CNN filter sizes')  # CNN
tf.flags.DEFINE_integer('num_filters', 128, 'Number of filters per filter size')  # CNN
tf.flags.DEFINE_integer('hidden_size', 128, 'Number of hidden units in the LSTM cell')  # RNN
tf.flags.DEFINE_integer('num_layers', 3, 'Number of the LSTM cells')  # RNN
tf.flags.DEFINE_float('keep_prob', 0.4, 'Dropout keep probability')
tf.flags.DEFINE_float('learning_rate', 1e-3, 'Learning rate')
tf.flags.DEFINE_float('l2_reg_lambda', 0.0, 'L2 regularization lambda')

# Training parameters
tf.flags.DEFINE_integer('batch_size', 64, 'Batch size')
tf.flags.DEFINE_integer('num_epochs', 50, 'Number of epochs')
tf.flags.DEFINE_integer('evaluate_every_steps', 100, 'Evaluate the model on validation set after this many steps')
tf.flags.DEFINE_integer('save_every_steps', 1000, 'Save the model after this many steps')
tf.flags.DEFINE_integer('num_checkpoint', 20, 'Number of models to store')

FLAGS = tf.flags.FLAGS

# Output files directory
timestamp = str(int(time.time()))
outdir = os.path.abspath(os.path.join(os.path.curdir, "runs", timestamp))
if not os.path.exists(outdir):
    os.makedirs(outdir)

# Save parameters to file
params = FLAGS.__flags
params_file = open(os.path.join(outdir, 'params.pkl'), 'wb')
pkl.dump(params, params_file, True)
params_file.close()


# Load data
# =============================================================================

data, labels, lengths, vocab_processor = data_helper.load_data(file_path=FLAGS.data_file,
                                                               sw_path=FLAGS.stop_word_file,
                                                               min_frequency=FLAGS.min_frequency,
                                                               max_length=FLAGS.max_length,
                                                               language=FLAGS.language,
                                                               shuffle=True)

# Save vocabulary processor
vocab_processor.save(os.path.join(outdir, 'vocab'))

FLAGS.vocab_size = len(vocab_processor.vocabulary_._mapping)

FLAGS.max_length = vocab_processor.max_document_length


# Simple Cross validation
# TODO use k-fold cross validation
x_train, x_valid, y_train, y_valid, train_lengths, valid_lengths = train_test_split(data,
                                                                                    labels,
                                                                                    lengths,
                                                                                    test_size=FLAGS.test_size,
                                                                                    random_state=22)
# Batch iterator
train_data = data_helper.batch_iter(x_train, y_train, train_lengths, FLAGS.batch_size, FLAGS.num_epochs)
flags_dict = sorted(tf.flags.FLAGS.__flags.items(), key=lambda x: x[0])
# Print parameters
print('Parameters:')
for item in flags_dict:
    print('{}: {}'.format(item[0], item[1]))
print('')

# Train
# =============================================================================

with tf.Graph().as_default():
    with tf.Session() as sess:
        if FLAGS.clf == 'cnn':
            classifier = cnn_clf(FLAGS)
        elif FLAGS.clf == 'rnn':
            classifier = rnn_clf(FLAGS)
        elif FLAGS.clf == 'clstm':
            classifier = clstm_clf(FLAGS)
        else:
            raise ValueError('clf should be one of [cnn, rnn, clstm]')

        # Train procedure
        global_step = tf.Variable(0, name='global_step', trainable=False)
        optimizer = tf.train.AdamOptimizer(FLAGS.learning_rate)
        grads_and_vars = optimizer.compute_gradients(classifier.cost)
        train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

        # Summaries
        loss_summary = tf.summary.scalar('Loss', classifier.cost)
        accuracy_summary = tf.summary.scalar('Accuracy', classifier.accuracy)

        # Train summary
        train_summary_op = tf.summary.merge_all()
        train_summary_dir = os.path.join(outdir, 'summaries', 'train')
        train_summary_writer = tf.summary.FileWriter(train_summary_dir, sess.graph)

        # Validation summary
        valid_summary_op = tf.summary.merge_all()
        valid_summary_dir = os.path.join(outdir, 'summaries', 'valid')
        valid_summary_writer = tf.summary.FileWriter(valid_summary_dir, sess.graph)

        saver = tf.train.Saver(max_to_keep=FLAGS.num_checkpoint)

        sess.run(tf.global_variables_initializer())


        def run_step(input_data, is_training=True):
            """Run one step of the training process."""
            input_x, input_y, sequence_length = input_data

            fetches = {'step': global_step,
                       'cost': classifier.cost,
                       'accuracy': classifier.accuracy}
            feed_dict = {classifier.input_x: input_x,
                         classifier.input_y: input_y}

            if FLAGS.clf != 'cnn':
                fetches['final_state'] = classifier.final_state
                feed_dict[classifier.batch_size] = len(input_x)
                feed_dict[classifier.sequence_length] = sequence_length

            if is_training:
                fetches['train_op'] = train_op
                fetches['summaries'] = train_summary_op
                feed_dict[classifier.keep_prob] = FLAGS.keep_prob
            else:
                fetches['summaries'] = valid_summary_op
                feed_dict[classifier.keep_prob] = 1.0

            vars = sess.run(fetches, feed_dict)
            step = vars['step']
            cost = vars['cost']
            accuracy = vars['accuracy']
            summaries = vars['summaries']

            # Write summaries to file
            if is_training:
                train_summary_writer.add_summary(summaries, step)
            else:
                valid_summary_writer.add_summary(summaries, step)

            time_str = datetime.datetime.now().isoformat()
            print("{}: step: {}, loss: {:g}, accuracy: {:g}".format(time_str, step, cost, accuracy))

            return accuracy


        print('Start training ...')

        for train_input in train_data:
            run_step(train_input, is_training=True)
            current_step = tf.train.global_step(sess, global_step)

            if current_step % FLAGS.evaluate_every_steps == 0:
                print('\nValidation')
                run_step((x_valid, y_valid, valid_lengths), is_training=False)
                print('')

            if current_step % FLAGS.save_every_steps == 0:
                save_path = saver.save(sess, os.path.join(outdir, 'model/clf'), current_step)

        print('\nAll the files have been saved to {}\n'.format(outdir))
