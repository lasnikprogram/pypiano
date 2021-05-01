# -*- coding: utf-8 -*-
"""
"""
import logging
import pkg_resources
import time

from mingus.containers import Note, NoteContainer, Bar, Track
from mingus.midi.fluidsynth import FluidSynthSequencer
from mingus.midi import pyfluidsynth as fs

from typing import Union
from pathlib import Path
from .keyboard import PianoKeyboard, PianoKey
from .utils import PianoUtils

DEFAULT_SOUND_FONTS = Path(
    pkg_resources.resource_filename("pyano", "/sound_fonts/FluidR3_GM.sf2")
)

# Valid audio driver are taken from docstring of mingus.midi.fluidsynth.FluidSynthSequencer.start_audio_output() method
# https://github.com/bspaans/python-mingus/blob/f131620eb7353bcfbf1303b24b951a95cad2ac20/mingus/midi/fluidsynth.py#L57
VALID_AUDIO_DRIVERS = (
    None,
    "alsa",
    "oss",
    "jack",
    "portaudio",
    "sndmgr",
    "coreaudio",
    "Direct Sound",
    "dsound",
    "pulseaudio",
)

# See a list of General Midi instruments here https://en.wikipedia.org/wiki/General_MIDI. Pianos are in section one
DEFAULT_INSTRUMENTS = {
    "Acoustic Grand Piano": 0,
    "Bright Acoustic Piano": 1,
    "Electric Grand Piano": 2,
    "Honky-tonk Piano": 3,
    "Electric Piano 1": 4,
    "Electric Piano 2": 5,
    "Harpsichord": 6,
    "Clavi": 7,
}

# Initialize module logger
logger = logging.getLogger("pyano")
logger.addHandler(logging.NullHandler())


class Piano(FluidSynthSequencer):
    """Class representing a Piano with 88 keys based on mingus

    Class to programmatically play piano via audio output or record music to a wav file. Abstraction layer on top of
    mingus.midi.fluidsynth.FluidSynthSequencer.

    Attributes
    ----------
    sound_fonts_path : Union[str, Path]
        Optional string or Path object pointing to a *.sf2 files. Pyano ships sound fonts by default
    audio_driver: Union[str, None]
        Optional argument specifying audio driver to use. Following audio drivers could be used:
            (None, "alsa", "oss", "jack", "portaudio", "sndmgr", "coreaudio","Direct Sound", "dsound", "pulseaudio").
        Not all drivers will be available for every platform
    instrument: Union[str, int]
        Optional argument to set the instrument that should be used. If default sound fonts are used you can choose
        one of the following pianos sounds:
            ("Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
             "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavi")
        If different sound fonts are provided you should pass an integer with the instrument number
    """

    def __init__(
        self,
        sound_fonts_path: Union[str, Path] = DEFAULT_SOUND_FONTS,
        audio_driver: Union[str, None] = None,
        instrument: Union[str, int] = "Acoustic Grand Piano",
    ) -> None:

        super().__init__()

        self._sound_fonts_path = Path(sound_fonts_path)
        # Set variable to track if sound fonts are loaded
        self._sound_fonts_loaded = False
        self.load_sound_fonts(self._sound_fonts_path)

        # Audio output is lazily loaded when self.play method is called
        self._current_audio_driver = audio_driver
        # Set a variable to to track if audio output is currently active
        self._audio_driver_is_active = False

        # Set instrument
        self.instrument = instrument
        self.load_instrument(self.instrument)

        # Initialize a piano keyboard
        self.keyboard = PianoKeyboard()

    def _unload_sound_fonts(self) -> None:
        """Unload a given sound font file

        Safely unload current sound font file. Method controls for if a sound font file is already loaded via
        self._sound_fonts_loaded.
        """
        logger.debug(
            "Unloading current active sound fonts from file: {0}".format(
                self._sound_fonts_path
            )
        )

        if self._sound_fonts_loaded:
            self.fs.sfunload(self.sfid)
        else:
            logger.debug("No active sound fonts")

    def load_sound_fonts(self, sound_fonts_path: Union[str, Path]) -> None:
        """Load sound fonts from a given path"""
        logger.debug(
            "Attempting to load sound fonts from {file}".format(file=sound_fonts_path)
        )

        if self._sound_fonts_loaded:

            self._unload_sound_fonts()

        if not self.load_sound_font(str(sound_fonts_path)):
            raise Exception(
                "Could not load sound fonts from {file}".format(file=sound_fonts_path)
            )

        self._sound_fonts_loaded = True
        self._sound_fonts_path = sound_fonts_path

        logger.debug(
            "Successfully initialized sound fonts from {file_path}".format(
                file_path=sound_fonts_path
            )
        )

    def _start_audio_output(self) -> None:
        """Private method to start audio output

        This method in conjunction with self._stop_audio_output should safely start and stop audio output for example
        when there is switch between audio output and recording audio to a file (check doc string of
        self._stop_audio_output for more details why this necessary)
        """

        logger.debug(
            "Starting audio output using driver: {driver}".format(
                driver=self._current_audio_driver
            )
        )

        # That is actually already done by the low level method
        if self._current_audio_driver not in VALID_AUDIO_DRIVERS:
            raise ValueError(
                "{driver} is not a valid audio driver. Must be one of: {allowed_drivers}".format(
                    driver=self._current_audio_driver,
                    allowed_drivers=VALID_AUDIO_DRIVERS,
                )
            )
        if not self._audio_driver_is_active:
            self.start_audio_output(self._current_audio_driver)
            # It seems to be necessary to reset the program after starting audio output
            self.fs.program_reset()
            self._audio_driver_is_active = True
        else:
            logger.debug("Audio output seems to be already active")

    def _stop_audio_output(self) -> None:
        """Private method to stop audio output

        Method is used to safely stop audio output via deleting an active audio driver, for example if there
        is a switch between audio output and recording. This method should be used in conjunction with
        self._start_audio_output(). It is a thin wrapper around the  mingus.midi.pyfluidsynth.delete_fluid_audio_driver
        and ensures that mingus.midi.pyfluidsynth.delete_fluid_audio_driver is not called twice because this seems to
        result in segmentation fault:

            [1]    4059 segmentation fault  python3

        Tracking is done via checking and setting self._audio_driver_is_active attribute. This method basically
        replaces mingus.midi.pyfluidsynth.delete() (which is also basically a wrapper for
        mingus.midi.pyfluidsynth.delete_fluid_audio_driver), because the method from the mingus package is not safe to
        use and results in a crash if for some reason is called after an audio driver was already deleted and there
        isn't currently an active one. Despite the mingus.midi.pyfluidsynth.delete method seems to attempt to check if
        an audio driver is present and tries to avoid such a scenario via checking
        mingus.midi.pyfluidsynth.audio_driver argument for None, however once an audio driver was initialized the
        audio_driver argument seems to be never set back to None and therefore it seems you can't rely on checking that
        argument to know if an audio is active.

        I am not sure if it is a good way to do it that way and if it has any side effects, but it seems to work so far
        and enables switching between recording to a file and playing audio output without initializing a new object.
        """
        if self._audio_driver_is_active:
            fs.delete_fluid_audio_driver(self.fs.audio_driver)
            # It seems to be necessary to reset the program after starting audio output
            self.fs.program_reset()
            self._audio_driver_is_active = False
        else:
            logger.debug("Audio output seems to be already inactive")

    def load_instrument(self, instrument: Union[str, int]) -> None:
        """Method to change the piano instrument

        Load an instrument that should be used. If pyano default sound fonts are used you can choose one of the
        following instruments:
            ("Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
             "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavi")
        Args
            instrument: String with the name of the instrument to be used for default sound founts. If different sound
                        fonts are provided you should provide an integer with the instrument number.
        """
        logger.info("Setting instrument: {0}".format(instrument))

        # If default sound fonts are used, check if the provided instrument string is contained in the valid
        # instruments. If different sound fonts are provided, checks are disabled
        if self._sound_fonts_path == DEFAULT_SOUND_FONTS:

            if instrument not in tuple(DEFAULT_INSTRUMENTS.keys()):
                raise ValueError(
                    "Unknown instrument parameter. Instrument must be one of: {instrument}".format(
                        instrument=tuple(DEFAULT_INSTRUMENTS.keys())
                    )
                )

            self.set_instrument(
                channel=1, instr=DEFAULT_INSTRUMENTS[instrument], bank=0
            )
            self.instrument = instrument

        else:

            self.set_instrument(channel=1, instr=instrument, bank=0)
            self.instrument = instrument

    def play(
        self,
        music_container: Union[str, int, Note, NoteContainer, Bar, Track, PianoKey],
        recording_file: Union[str, None] = None,
        record_seconds: int = 4,
        stop_recording: bool = True,
    ) -> None:
        """Function to play a provided music container and control recording settings

        Main user facing method of pyano.piano class. Handles setting up audio output or recording to audio file.
        and handles switch between audio playing and recording to wav file

        Args
            music_container: A music container such as Notes, NoteContainers, etc. describing a piece of music
            recording_file: Path to a wav file where audio should be saved to
            record_seconds: The duration of recording in seconds
            stop_recording: Parameter indicating whether file should be closed after playing music_container
        """

        self._lint_music_container(music_container)

        if recording_file is None:

            logger.info(
                "Playing music container: {music_container} via audio".format(
                    music_container=music_container
                )
            )
            self._start_audio_output()
            self._play_music_container(music_container)

        else:

            logger.info(
                "Recording music container: {music_container} to file {recording_file}".format(
                    music_container=music_container, recording_file=recording_file
                )
            )
            self._stop_audio_output()
            self.start_recording(recording_file)
            self._play_music_container(music_container)
            samples = fs.raw_audio_string(
                self.fs.get_samples(int(record_seconds * 44100))
            )
            self.wav.writeframes(bytes(samples))

            # Close the recording file
            if stop_recording:
                self.wav.close()
                self._start_audio_output()
            else:
                logger.info(
                    "Finished recording to {recording_file}".format(
                        recording_file=recording_file
                    )
                )

    def _play_music_container(
        self,
        music_container: Union[str, int, Note, NoteContainer, Bar, Track, PianoKey],
    ) -> None:
        """Private method to call the appropriate low level play method for given music container class

        mingus.midi.fluidsynth exposes a few different methods to play different music containers, such as Notes or
        NoteContainers, etc. This should be abstracted for the user and this function calls the appropriate low level
        play method from mingus.midi.fluidsynth

        Args
            music_container: A music container such as Notes, NoteContainers, etc. describing a piece of music
        """

        logger.debug(
            "Attempting to play music container: {music_container} of type: {container_type}".format(
                music_container=music_container,
                container_type=str(type(music_container)),
            )
        )

        if isinstance(music_container, str):
            self.play_Note(Note(music_container))
        elif isinstance(music_container, int):
            self.play_Note(self.keyboard[music_container].get_as_note())
        elif isinstance(music_container, Note):
            self.play_Note(music_container)
        elif isinstance(music_container, NoteContainer):
            self.play_NoteContainer(music_container)
        elif isinstance(music_container, Bar):
            self.play_Bar(music_container)
        elif isinstance(music_container, Track):
            self.play_Track(music_container)

        logger.debug(
            "Done playing music container: {music_container} of type: {container_type}".format(
                music_container=music_container,
                container_type=str(type(music_container)),
            )
        )

    def _lint_music_container(
        self, music_container: Union[str, Note, NoteContainer, Bar, Track]
    ) -> None:
        """Check a music container for invalid notes

        Method checks a given music container like mingus.containers.Note or more complex containers like Tracks, etc.
        for notes that can't be found on a piano with 88 keys. In case a string is passed it also checks whether it can
        be parsed as a mingus.containers.Note.

        Args
            music_container: A music container such as Notes, NoteContainers, etc. describing a piece of music

        Raises
            ValueError: If illegal notes in given music container are found
        """

        logger.debug(
            "Checking music container: {container} of class {container_type} for invalid notes".format(
                container=music_container, container_type=str(type(music_container))
            )
        )

        if isinstance(music_container, str):
            note = Note(music_container)
            distinct_notes_in_container = {PianoUtils.note_to_string(note)}
        elif isinstance(music_container, Note):
            distinct_notes_in_container = {PianoUtils.note_to_string(music_container)}
        elif isinstance(music_container, NoteContainer):
            distinct_notes_in_container = set(
                PianoUtils.note_container_to_note_string_list(music_container)
            )
        elif isinstance(music_container, Bar):
            distinct_notes_in_container = set(
                PianoUtils.bar_to_note_string_list(music_container)
            )
        elif isinstance(music_container, Track):
            distinct_notes_in_container = set(
                PianoUtils.track_to_note_string_list(music_container)
            )
        else:
            raise Exception("Unexpected Error")

        diff = distinct_notes_in_container - self.keyboard.distinct_key_names
        if len(diff) > 0:
            raise ValueError(
                "Found notes that are not on a piano with 88 keys. Invalid notes in container: {0}".format(
                    diff
                )
            )

        logger.debug(
            "Music container: {container} of class {container_type} looks good".format(
                container=music_container, container_type=str(type(music_container))
            )
        )

    def stop(
        self, music_container: Union[str, Note, NoteContainer, Bar, Track, PianoKey]
    ) -> None:
        """Currently not working"""
        if isinstance(music_container, str):
            try:
                note = Note(music_container)
                self.stop_Note(note)
            except Exception as e:
                raise ValueError(
                    "If a string is passed it must have the form <NOTE_NAME><ACCIDENTAL>-<Ocatave>"
                )
        elif isinstance(music_container, NoteContainer):
            self.stop_NoteContainer(music_container)
        else:
            # At the moment there is no dedicated low level method to stop Bars and Tracks, therefore in case of
            # A Bar or Track is passed, everything will be stopped
            self.stop_everything()

    @staticmethod
    def pause(seconds: int) -> None:
        """Pause further execution for a given time

        Args
            duration: Time to pause further execution in seconds
        """
        time.sleep(seconds)