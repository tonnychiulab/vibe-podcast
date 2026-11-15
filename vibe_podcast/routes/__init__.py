"""FastAPI route modules for VibePodcast Studio."""


def get_server():
    """Return the vibe_podcast.server module lazily to avoid circular imports at module load time."""
    import vibe_podcast.server as srv

    return srv
