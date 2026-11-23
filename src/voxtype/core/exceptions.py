"""Domain exceptions shown as human-readable failures at the UI boundary."""


class VoxTypeError(Exception):
    """Base class for expected application failures."""


class InvalidStateTransition(VoxTypeError):
    """Raised when a transition would make runtime state contradictory."""


class AudioDeviceError(VoxTypeError):
    """Raised when an input device cannot be opened or disappears."""


class MicrophoneAccessDenied(AudioDeviceError):
    """Raised when opening the microphone is explicitly denied."""


class ModelUnavailableError(VoxTypeError):
    """Raised when the selected local model cannot be used."""


class TextInjectionError(VoxTypeError):
    """Raised when committed text could not safely reach its target."""


class TargetUnavailableError(TextInjectionError):
    """Raised when a locked target is gone, inactive, or unsafe."""


class SettingsError(VoxTypeError):
    """Raised for settings persistence failures after safe fallback handling."""


class SessionError(VoxTypeError):
    """Raised when a session journal transaction fails."""
