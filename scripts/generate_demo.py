"""Generate a complete dual-host demo episode from a real technical article."""

from pathlib import Path

from vibe_podcast.scriptwriter import generate_podcast_script
from vibe_podcast.synthesizer import synthesize_podcast

DEMO_ARTICLE = """
# VibeVoice Technical Deep Dive: 7.5 Hz Continuous Speech Tokenizers & Next-Token Diffusion

VibeVoice is an open-source frontier voice AI framework developed by Microsoft Research, designed specifically to overcome the limitations of traditional Text-to-Speech (TTS) systems when generating long-form, multi-speaker conversational audio such as podcasts.

## 1. Why Traditional Audio Tokenizers Fail at Scale
Traditional acoustic tokenizers like EnCodec, SoundStream, or HuBERT typically operate at frame rates between 50 Hz and 100 Hz (50 to 100 tokens per second). If you try to generate a 30-minute or 90-minute podcast at 50 Hz+, the sequence length explodes into hundreds of thousands of tokens, overwhelming LLM context windows and causing speaker timbre drift and broken turn-taking.

## 2. The Core Breakthrough: 7.5 Hz Ultra-Low Frame Rate Tokenizers
Microsoft Research engineered continuous speech tokenizers (both Acoustic and Semantic) operating at just 7.5 Hz—compressing 24 kHz raw audio by roughly 3,200x (about 80x more compact than EnCodec).
- Acoustic Tokenizer: Uses a sigma-VAE mirror-symmetric encoder-decoder with modified Transformer blocks, reducing 1 second of audio into only 7.5 continuous tokens.
- Semantic Tokenizer: Mirrors the encoder layout without VAE components and is trained via an ASR proxy task to preserve linguistic structure.
As a result, a 90-minute multi-speaker conversation shrinks to ~40,500 tokens, fitting comfortably inside a standard 64K LLM context window.

## 3. Next-Token Diffusion Framework
Dropping to 7.5 Hz creates a new challenge: preserving micro-intonations, breath sounds, pauses, and distinct speaker identities. VibeVoice solves this with Next-Token Diffusion:
- The LLM Backbone (Qwen2.5-1.5B) acts as the "director", understanding textual context, dialogue flow, and speaker turn-taking.
- A lightweight Diffusion Head (~4 Transformer layers, ~40M-123M parameters) conditioned on LLM hidden states reconstructs high-fidelity 24 kHz acoustic details, including natural conversational cues like breaths and laughter hints.
"""


def main() -> None:
    episodes_dir = Path(__file__).resolve().parent.parent / "episodes"
    print("1/2 Generating dual-host script via agy CLI...")
    script = generate_podcast_script(
        source_text=DEMO_ARTICLE,
        host_a="志明",
        host_b="春嬌",
        language="zh-TW",
        target_turns=12,
    )
    print(f"    Title: {script.title}")
    print(f"    Turns: {len(script.turns)}")

    print("2/2 Synthesizing dual-host podcast audio & timeline...")
    result = synthesize_podcast(
        script=script,
        output_root=episodes_dir,
        episode_id="demo-vibevoice-deep-dive",
        engine="edge_dialogue",
        language="zh-TW",
    )
    print(f"    Done! Audio: {result.audio_path}")
    print(f"    Duration: {result.duration_sec}s, Segments: {len(result.segments)}")


if __name__ == "__main__":
    main()
