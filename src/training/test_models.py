import tensorflow as tf
import numpy as np
import pytest


from training.models import Text2ImageMatchingModel


@pytest.fixture
def rnn_hidden_size():
    return 50


@pytest.fixture
def input_images():
    return np.random.rand(3, 224, 224, 3).astype(np.float32)


@pytest.fixture
def seed():
    return 42


@pytest.fixture
def captions():
    return np.array([[2, 5, 4, 6, 8], [9, 11, 0, 0, 0], [12, 7, 3, 10, 0]])


@pytest.fixture
def captions_len():
    return np.array([5, 2, 4])


@pytest.fixture
def labels():
    return np.array([1, 1, 2])


@pytest.fixture
def margin():
    return 0.0


@pytest.fixture
def vocab_size():
    return 13


@pytest.fixture
def embedding_size():
    return 5


@pytest.fixture
def cell_type():
    return "lstm"


@pytest.fixture
def num_layers():
    return 2


@pytest.fixture
def keep_prob():
    return 0.5


@pytest.fixture
def attn_size():
    return 10


@pytest.fixture
def encoded_input():
    return np.random.rand(5, 10, 100).astype(np.float32)


@pytest.fixture
def optimizer_type():
    return "adam"


@pytest.fixture
def learning_rate():
    return 0.0


@pytest.fixture
def clip_value():
    return 0.0


def test_image_encoder(input_images, rnn_hidden_size):
    tf.reset_default_graph()
    input_layer = tf.placeholder(dtype=tf.float32, shape=[3, 224, 224, 3])
    image_encoded = Text2ImageMatchingModel.image_encoder_graph(
        input_layer, rnn_hidden_size
    )
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        output_shape = sess.run(
            image_encoded, feed_dict={input_layer: input_images}
        ).shape
    assert output_shape[0] == 3
    assert output_shape[2] == 2 * rnn_hidden_size


def test_text_encoder(
    seed,
    captions,
    captions_len,
    vocab_size,
    embedding_size,
    cell_type,
    rnn_hidden_size,
    num_layers,
    keep_prob,
):
    tf.reset_default_graph()
    text_encoded = Text2ImageMatchingModel.text_encoder_graph(
        seed,
        captions,
        captions_len,
        vocab_size,
        embedding_size,
        cell_type,
        rnn_hidden_size,
        num_layers,
        keep_prob,
    )
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        outputs = sess.run(text_encoded).shape
    assert outputs[0] == 3
    assert outputs[1] == 5
    assert outputs[2] == 2 * rnn_hidden_size


def test_joint_attention(rnn_hidden_size, attn_size, encoded_input):
    tf.reset_default_graph()
    input_layer = tf.placeholder(dtype=tf.float32, shape=[5, 10, 100])
    attention = Text2ImageMatchingModel.join_attention_graph(attn_size, input_layer)
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        attended_input, alphas = sess.run(
            attention, feed_dict={input_layer: encoded_input}
        )
        assert attended_input.shape[0] == 5
        assert attended_input.shape[1] == rnn_hidden_size * 2


def test_attended_image_text_shape(
    seed,
    input_images,
    captions,
    captions_len,
    margin,
    rnn_hidden_size,
    vocab_size,
    embedding_size,
    cell_type,
    num_layers,
    attn_size,
    optimizer_type,
    learning_rate,
    clip_value,
):
    tf.reset_default_graph()
    model = Text2ImageMatchingModel(
        seed,
        input_images,
        captions,
        captions_len,
        margin,
        rnn_hidden_size,
        vocab_size,
        embedding_size,
        cell_type,
        num_layers,
        attn_size,
        optimizer_type,
        learning_rate,
        clip_value,
        "",
    )
    assert model.attended_images.shape[0] == model.attended_captions.shape[0]
    assert model.attended_images.shape[1] == model.attended_captions.shape[1]
