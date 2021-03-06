import tensorflow as tf
import argparse
import logging
from tqdm import tqdm
import os
import absl.logging

from utils.datasets import PascalSentencesDataset
from multi_hop_attention.hyperparameters import YParams
from multi_hop_attention.loaders import TrainValLoader
from multi_hop_attention.models import MultiHopAttentionModel
from utils.evaluators import Evaluator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
tf.logging.set_verbosity(tf.logging.ERROR)

# https://github.com/abseil/abseil-py/issues/99
absl.logging.set_verbosity("info")
absl.logging.set_stderrthreshold("info")


def train(
    hparams_path: str,
    images_path: str,
    texts_path: str,
    epochs: int,
    recall_at: int,
    batch_size: int,
    prefetch_size: int,
    save_model_path: str,
    log_model_path: str,
    decay_rate_epochs: int,
    batch_hard: bool,
    learning_rate: float = None,
    frob_norm_pen: float = None,
    attn_hops: int = None,
) -> None:
    """Starts a training session with the Pascal1k sentences dataset.

    Args:
        hparams_path: The path to the hyperparameters yaml file.
        images_path: A path where all the images are located.
        texts_path: Path where the text doc with the descriptions is.
        epochs: The number of epochs to train the model excluding the vgg.
        recall_at: Validate on recall at K.
        batch_size: The batch size to be used.
        prefetch_size: How many batches to keep on GPU ready for processing.
        save_model_path: Where to save the model.
        log_model_path: Where to log the summaries.
        learning_rate: If provided update the one in hparams.
        frob_norm_pen: If provided update the one in hparams.
        attn_hops: If provided update the one in hparams.
        batch_hard: Whether to train only on the hardest negatives.
        decay_rate_epochs: When to decay the learning rate.

    Returns:
        None

    """
    hparams = YParams(hparams_path)
    # If learning rate is provided update the hparams learning rate
    if learning_rate is not None:
        hparams.set_hparam("learning_rate", learning_rate)
    # If frob_norm_pen is provided update the hparams frob_norm_pen
    if frob_norm_pen is not None:
        hparams.set_hparam("frob_norm_pen", frob_norm_pen)
    # If attn_hops is provided update the hparams attn_hops
    if attn_hops is not None:
        hparams.set_hparam("attn_hops", attn_hops)
    dataset = PascalSentencesDataset(images_path, texts_path)
    train_image_paths, train_captions = dataset.get_train_data()
    val_image_paths, val_captions = dataset.get_val_data()
    logger.info("Train dataset created...")
    logger.info("Validation dataset created...")

    evaluator_train = Evaluator()
    evaluator_val = Evaluator(
        len(val_image_paths), hparams.joint_space * hparams.attn_hops
    )

    logger.info("Evaluators created...")

    # Resetting the default graph and setting the random seed
    tf.reset_default_graph()
    tf.set_random_seed(hparams.seed)

    loader = TrainValLoader(
        train_image_paths,
        train_captions,
        val_image_paths,
        val_captions,
        batch_size,
        prefetch_size,
    )
    images, captions, captions_lengths = loader.get_next()
    logger.info("Loader created...")

    decay_steps = decay_rate_epochs * len(train_image_paths) / batch_size
    model = MultiHopAttentionModel(
        images,
        captions,
        captions_lengths,
        hparams.margin,
        hparams.joint_space,
        hparams.num_layers,
        hparams.attn_size,
        hparams.attn_hops,
        hparams.learning_rate,
        hparams.gradient_clip_val,
        decay_steps,
        batch_hard,
        log_model_path,
        hparams.name,
    )
    logger.info("Model created...")
    logger.info("Training is starting...")

    with tf.Session() as sess:

        # Initializers
        model.init(sess)
        model.add_summary_graph(sess)

        for e in range(epochs):
            # Reset evaluators
            evaluator_train.reset_all_vars()
            evaluator_val.reset_all_vars()

            # Initialize iterator with train data
            sess.run(loader.train_init)
            try:
                with tqdm(total=len(train_image_paths)) as pbar:
                    while True:
                        _, loss, lengths = sess.run(
                            [model.optimize, model.loss, model.captions_len],
                            feed_dict={
                                model.frob_norm_pen: hparams.frob_norm_pen,
                                model.keep_prob: hparams.keep_prob,
                                model.weight_decay: hparams.weight_decay,
                            },
                        )
                        evaluator_train.update_metrics(loss)
                        pbar.update(len(lengths))
                        pbar.set_postfix({"Batch loss": loss})
            except tf.errors.OutOfRangeError:
                pass

            # Initialize iterator with validation data
            sess.run(loader.val_init)
            try:
                with tqdm(total=len(val_image_paths)) as pbar:
                    while True:
                        loss, lengths, embedded_images, embedded_captions = sess.run(
                            [
                                model.loss,
                                model.captions_len,
                                model.attended_images,
                                model.attended_captions,
                            ]
                        )
                        evaluator_val.update_metrics(loss)
                        evaluator_val.update_embeddings(
                            embedded_images, embedded_captions
                        )
                        pbar.update(len(lengths))
            except tf.errors.OutOfRangeError:
                pass

            if evaluator_val.is_best_image2text_recall_at_k(recall_at):
                evaluator_val.update_best_image2text_recall_at_k()
                logger.info("=============================")
                logger.info(
                    f"Found new best on epoch {e+1} with recall at {recall_at}: "
                    f"{evaluator_val.best_image2text_recall_at_k}! Saving model..."
                )
                logger.info("=============================")
                model.save_model(sess, save_model_path)
            else:
                logger.info(
                    f"On epoch {e + 1} the recall at {recall_at} is: "
                    f"{evaluator_val.cur_image2text_recall_at_k} :("
                )

            # Write multi_hop_attention summaries
            train_loss_summary = sess.run(
                model.train_loss_summary,
                feed_dict={model.train_loss_ph: evaluator_train.loss},
            )
            model.add_summary(sess, train_loss_summary)

            # Write validation summaries
            val_loss_summary, val_recall_at_k = sess.run(
                [model.val_loss_summary, model.val_recall_at_k_summary],
                feed_dict={
                    model.val_loss_ph: evaluator_val.loss,
                    model.val_recall_at_k_ph: evaluator_val.cur_image2text_recall_at_k,
                },
            )
            model.add_summary(sess, val_loss_summary)
            model.add_summary(sess, val_recall_at_k)


def main():
    # Without the main sentinel, the code would be executed even if the script were
    # imported as a module.
    args = parse_args()
    train(
        args.hparams_path,
        args.images_path,
        args.texts_path,
        args.epochs,
        args.recall_at,
        args.batch_size,
        args.prefetch_size,
        args.save_model_path,
        args.log_model_path,
        args.decay_rate_epochs,
        args.batch_hard,
        args.learning_rate,
        args.frob_norm_pen,
        args.attn_hops,
    )


def parse_args():
    """Parse command line arguments.

    Returns:
        Arguments

    """
    parser = argparse.ArgumentParser(
        description="Performs multi_hop_attention on the Pascal sentences dataset."
    )
    parser.add_argument(
        "--hparams_path",
        type=str,
        default="hyperparameters/default_hparams.yaml",
        help="Path to a hyperparameters yaml file.",
    )
    parser.add_argument(
        "--images_path",
        type=str,
        default="data/Pascal_sentences_dataset/dataset",
        help="Path where all images are.",
    )
    parser.add_argument(
        "--texts_path",
        type=str,
        default="data/Pascal_sentences_dataset/sentence",
        help="Path to the file where the image to caption mappings are.",
    )
    parser.add_argument(
        "--checkpoint_path", type=str, default=None, help="Path to a model checkpoint."
    )
    parser.add_argument(
        "--log_model_path",
        type=str,
        default="logs/tryout",
        help="Where to log the summaries.",
    )
    parser.add_argument(
        "--save_model_path",
        type=str,
        default="models/tryout",
        help="Where to save the model.",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=5,
        help="The number of epochs to train the model excluding the vgg.",
    )
    parser.add_argument(
        "--recall_at", type=int, default=10, help="Validate on recall at."
    )
    parser.add_argument(
        "--batch_size", type=int, default=64, help="The size of the batch."
    )
    parser.add_argument(
        "--prefetch_size", type=int, default=5, help="The size of prefetch on gpu."
    )
    parser.add_argument(
        "--learning_rate",
        type=float,
        default=None,
        help="This will override the hparams learning rate.",
    )
    parser.add_argument(
        "--frob_norm_pen",
        type=float,
        default=None,
        help="This will override the hparams frob norm penalization rate.",
    )
    parser.add_argument(
        "--attn_hops",
        type=int,
        default=None,
        help="This will override the hparams attention heads.",
    )
    parser.add_argument(
        "--decay_rate_epochs",
        type=int,
        default=4,
        help="When to decay the learning rate.",
    )
    parser.add_argument("--batch_hard", action="store_true")

    return parser.parse_args()


if __name__ == "__main__":
    main()
