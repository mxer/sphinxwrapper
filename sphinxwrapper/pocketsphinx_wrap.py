"""
This module contains a Pocket Sphinx Decoder subclass with some simplified methods
and properties.
"""

from pocketsphinx import Decoder, Config
from .config import set_hmm_and_dict_paths, search_arguments_set, set_lm_path,\
    ConfigError
import tempfile
import os
import numbers


class PocketSphinxError(Exception):
    pass


class PocketSphinx(Decoder):
    """
    Pocket Sphinx decoder subclass with processing methods that provide callback
    functionality, amongst other things.

    This class will try to set required config options, such as '-hmm', '-dict'
    and/or '-lm', in the config object automatically if they are not set.
    """
    # Internal values used in process_audio to keep track of the utterance state
    # between method calls
    _UTT_IDLE = object()
    _UTT_STARTED = object()
    _UTT_ENDED = object()

    def __init__(self, config=Decoder.default_config()):
        assert isinstance(config, Config)

        search_args_set = search_arguments_set(config)

        if len(search_args_set) == 0:
            # Use the language model by default if nothing else is set
            set_lm_path(config)
        elif len(search_args_set) > 1:
            raise ConfigError("more than one search argument was set in the Config "
                              "object")

        # Set the required config paths if they aren't already set
        if not (config.get_string("-hmm") and config.get_string("-dict")):
            set_hmm_and_dict_paths(config)

        self._speech_start_callback = None
        self._hypothesis_callback = None
        self._utterance_state = self._UTT_ENDED

        super(PocketSphinx, self).__init__(config)

    def process_audio(self, buf, no_search=False, full_utterance=False,
                      use_callbacks=True):
        """
        Process audio from an audio buffer using the decoder's process_raw method and
        call the speech start and hypothesis callbacks if and when necessary.
        """
        if self.utt_ended:
            self.start_utt()

        self.process_raw(buf, no_search, full_utterance)

        # Note that get_in_speech moves idle -> started if returning True, so check
        # utt_idle before calling that method.
        was_idle = self.utt_idle

        # Check if we're in speech.
        in_speech = self.get_in_speech()

        # In speech and idle -> started transition just occurred.
        if in_speech and was_idle and self.utt_started:
            # Call speech start callback if it is set
            if use_callbacks and self.speech_start_callback:
                self.speech_start_callback()

        elif not in_speech and self.utt_started:
            # We're not in speech any more; utterance is over.
            self.end_utt()
            hyp = self.hyp()

            # Call the hypothesis callback if using callbacks and if it is set
            if use_callbacks and self.hypothesis_callback:
                self.hypothesis_callback(hyp)
            elif not use_callbacks:
                return hyp

    def batch_process(self, buffers, no_search=False, full_utterance=False,
                      use_callbacks=True):
        """
        Process a list of audio buffers and return the speech hypothesis or use the
        decoder callbacks if use_callbacks is True.
        """
        result = None
        for buf in buffers:
            if use_callbacks:
                self.process_audio(buf, no_search, full_utterance, use_callbacks)
            else:
                processing_result = self.process_audio(
                    buf, no_search, full_utterance, use_callbacks)
                if processing_result:  # this'll be the hypothesis
                    result = processing_result

        return result

    def get_in_speech(self):
        """
        Check if the last audio buffer contained speech.
        This method will also move utterance state from idle to started.
        :rtype: bool
        """
        in_speech = super(PocketSphinx, self).get_in_speech()

        # Move idle -> started to make utterance properties compatible with using
        # methods like process_raw instead of process_audio.
        if in_speech and self.utt_idle:
            # Utterance has now started
            self._utterance_state = self._UTT_STARTED

        return in_speech

    def start_utt(self):
        """
        Starts a new utterance if one is not already in progress.
        This method will *not* raise an error if an utterance is in progress
        (started or idle) already.
        """
        if self.utt_ended:
            super(PocketSphinx, self).start_utt()
            self._utterance_state = self._UTT_IDLE

    @property
    def utt_idle(self):
        """
        Whether an utterance is in progress, but no speech has been detected yet.
        get_in_speech() would return False for this case.
        :rtype: bool
        """
        return self._utterance_state == self._UTT_IDLE

    @property
    def utt_started(self):
        """
        Whether an utterance is in progress and speech has been detected.
        get_in_speech() would return True for this case.
        :rtype: bool
        """
        return self._utterance_state == self._UTT_STARTED

    def end_utt(self):
        """
        Ends the current utterance if one was in progress.
        This method is useful for resetting processing of audio via the
        process_audio method. It will *not* raise an error if no utterance was in
        progress.
        """
        if not self.utt_ended:
            super(PocketSphinx, self).end_utt()
            self._utterance_state = self._UTT_ENDED

    @property
    def utt_ended(self):
        """
        Whether there is no utterance in progress.
        :rtype: bool
        """
        return self._utterance_state == self._UTT_ENDED

    # Alias utterance methods and properties
    end_utterance = end_utt
    start_utterance = start_utt
    utterance_started = utt_started
    utterance_idle = utt_idle
    utterance_ended = utt_ended

    def set_kws_list(self, name, kws_list):
        """
        Set a keywords Pocket Sphinx search with the specified name taking a
        keywords list as a Python dictionary. `kws_list` should be a dictionary of
        words to threshold value. It can also be a list or tuple of pairs:
        [(words, threshold value), (words, threshold value)...]

        This method generates a temporary keywords list file and calls the `set_kws`
        method with its path.
        :type name: str
        :param kws_list: list | dict
        """
        if not kws_list:
            return

        # If we get a list or tuple, turn it into a dict.
        if isinstance(kws_list, (list, tuple)):
            kws_list = dict(kws_list)

        # Get a new temporary file and write each words string and threshold value
        # on separate lines with the threshold value escaped with forward slashes.
        tf = tempfile.NamedTemporaryFile(mode="a", delete=False)
        for words, threshold in kws_list.items():
            if not isinstance(threshold, numbers.Number):
                raise PocketSphinxError("threshold value of '%s' for words '%s' is "
                                        "not a number" % (threshold, words))
            tf.write("%s /%s/\n" % (words, threshold))

        # Close the file and then set the search using the file's path.
        tf.close()
        self.set_kws(name, tf.name)

        # Delete the file manually.
        os.remove(tf.name)

    @property
    def active_search(self):
        """
        The name of the currently active Pocket Sphinx search.
        If the setter is passed a name with no matching Pocket Sphinx search, an
        error will be raised.
        :return: str
        """
        return self.get_search()

    @active_search.setter
    def active_search(self, value):
        self.set_search(value)

    @property
    def speech_start_callback(self):
        """
        Callback for when speech starts.
        """
        return self._speech_start_callback

    @speech_start_callback.setter
    def speech_start_callback(self, value):
        if not callable(value) and value is not None:
            raise TypeError("value must be callable or None")
        self._speech_start_callback = value

    @property
    def hypothesis_callback(self):
        """
        Callback called with Pocket Sphinx's hypothesis for what was said.
        """
        return self._hypothesis_callback

    @hypothesis_callback.setter
    def hypothesis_callback(self, value):
        if not callable(value) and value is not None:
            raise TypeError("value must be callable or None")
        self._hypothesis_callback = value
