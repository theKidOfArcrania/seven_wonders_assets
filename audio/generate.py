#!/usr/bin/env python3
"""Regenerate original Seven Wonders effects with FFmpeg, without external samples.

Requires Python 3 and FFmpeg with the aevalsrc filter. Run with no arguments to
regenerate wav/, or supply sound basenames to regenerate only those effects.
--check regenerates in a temporary directory and compares decoded PCM, leaving
the committed WAVs untouched. All temporary files are removed automatically.
"""

import argparse
import pathlib
import subprocess
import tempfile
import wave


def note(frequency, gain, start=0, decay=6, attack=150, harmonics=()):
    t = "t" if start == 0 else f"(t-{start})"
    partials = [f"sin(2*PI*{frequency}*{t})"]
    partials += [f"{weight}*sin(2*PI*{frequency * ratio:g}*{t})"
                 for ratio, weight in harmonics]
    signal = "+".join(partials)
    value = f"{gain}*({signal})*exp(-{decay}*{t})*(1-exp(-{attack}*{t}))"
    return value if start == 0 else f"if(gte(t,{start}),{value},0)"


def notes(*parts):
    return "+".join(parts)


# name: (expression, seconds, highpass Hz, lowpass Hz, attack seconds, fade start).
SOUNDS = {
    "table_enter": (
        "0.35*(sin(2*PI*(190*t+6*(1-exp(-40*t))))+0.3*sin(2*PI*460*t))*exp(-28*t)*(1-exp(-500*t))+if(gte(t,0.1),0.18*(sin(2*PI*392*(t-0.1))+0.3*sin(2*PI*784*(t-0.1)))*exp(-6*(t-0.1))*(1-exp(-120*(t-0.1))),0)+if(gte(t,0.23),0.13*sin(2*PI*587.33*(t-0.23))*exp(-6*(t-0.23))*(1-exp(-120*(t-0.23))),0)",
        0.9, 60, 4500, 0.003, 0.75),
    "player_join": (
        notes(note(523.25, 0.17, decay=9, attack=180, harmonics=((2, 0.22),)),
              note(659.25, 0.17, 0.12, 8, 180)),
        0.68, 100, 5000, 0.005, 0.55),
    "player_leave": (
        notes(note(523.25, 0.14, decay=9, attack=160),
              note(392, 0.14, 0.12, 8, 160, ((2, 0.18),))),
        0.68, 100, 4000, 0.005, 0.55),
    "ready": (
        notes(note(660, 0.18, decay=14, attack=250, harmonics=((2, 0.25),)),
              note(880, 0.14, 0.075, 14, 250)),
        0.38, 120, 5000, 0.003, 0.3),
    "unready": (
        notes(note(660, 0.14, decay=14, attack=250),
              note(440, 0.14, 0.075, 14, 250, ((2, 0.18),))),
        0.38, 120, 4500, 0.003, 0.3),
    "all_ready": (
        notes(note(523.25, 0.17), note(659.25, 0.17, 0.12),
              note(783.99, 0.17, 0.24, 5), note(1046.5, 0.13, 0.36, 5)),
        1.2, 100, 6000, 0.005, 1.0),
    "match_start": (
        "0.26*sin(2*PI*(110*t+2*(1-exp(-30*t))))*exp(-18*t)*(1-exp(-300*t))+if(gte(t,0.16),0.13*(sin(2*PI*261.63*(t-0.16))+0.22*sin(2*PI*523.26*(t-0.16)))*exp(-3*(t-0.16))*(1-exp(-55*(t-0.16))),0)+if(gte(t,0.3),0.13*sin(2*PI*392*(t-0.3))*exp(-3*(t-0.3))*(1-exp(-55*(t-0.3))),0)+if(gte(t,0.46),0.16*sin(2*PI*523.25*(t-0.46))*exp(-3*(t-0.46))*(1-exp(-55*(t-0.46))),0)",
        1.7, 45, 4500, 0.004, 1.4),
    "extra_play_available": (
        "0.2*sin(2*PI*659.25*t)*exp(-10*t)*(1-exp(-160*t))+if(gte(t,0.12),0.2*sin(2*PI*783.99*(t-0.12))*exp(-10*(t-0.12))*(1-exp(-160*(t-0.12))),0)+if(gte(t,0.27),0.21*(sin(2*PI*1046.5*(t-0.27))+0.15*sin(2*PI*2093*(t-0.27)))*exp(-6*(t-0.27))*(1-exp(-160*(t-0.27))),0)",
        1.1, 100, 6000, 0.005, 0.9),
    "low_time_warning": (
        notes(note(880, 0.19, decay=13, attack=200, harmonics=((2, 0.15),)),
              note(880, 0.19, 0.2, 13, 200, ((2, 0.15),)),
              note(659.25, 0.18, 0.4, 10, 200)),
        0.9, 120, 5000, 0.004, 0.75),
    "low_time_tick": (
        "(0.1*sin(2*PI*1450*t)+0.07*sin(2*PI*2310*t)+0.06*(random(0)-0.5))*exp(-100*t)*(1-exp(-1500*t))",
        0.09, 700, 4500, 0.001, 0.05),
    "action_rejected": (
        notes(note(293.66, 0.19, decay=12, attack=200, harmonics=((2, 0.16),)),
              note(220, 0.19, 0.1, 12, 200, ((2, 0.16),))),
        0.5, 75, 3200, 0.004, 0.38),
    "card_select": (
        "0.3*(random(0)-0.5)*(1-exp(-350*t))*exp(-45*t)*(0.65+0.35*sin(2*PI*55*t))+0.08*sin(2*PI*760*t)*exp(-60*t)*(1-exp(-600*t))",
        0.18, 500, 4800, 0.002, 0.12),
    "selection_cancel": (
        "0.18*(random(0)-0.5)*sin(PI*min(t/0.12,1))*exp(-10*t)+0.06*sin(2*PI*(540*t-600*t*t))*exp(-28*t)*(1-exp(-300*t))",
        0.22, 350, 3200, 0.003, 0.14),
    "action_submit": (
        notes(note(430, 0.2, decay=40, attack=700, harmonics=((2.5, 0.3),)),
              note(660, 0.09, 0.07, 20, 300)),
        0.3, 150, 4500, 0.002, 0.22),
    "structure_place": (
        "0.2*(random(0)-0.5)*sin(PI*min(t/0.12,1))+if(gte(t,0.1),(0.27*sin(2*PI*170*(t-0.1))+0.1*sin(2*PI*420*(t-0.1))+0.15*(random(1)-0.5))*exp(-35*(t-0.1))*(1-exp(-650*(t-0.1))),0)",
        0.42, 70, 4200, 0.003, 0.32),
    "card_discard": (
        "0.33*(random(0)-0.5)*sin(PI*min(t/0.22,1))*(0.7+0.3*sin(2*PI*35*t))+if(gte(t,0.18),0.15*sin(2*PI*210*(t-0.18))*exp(-45*(t-0.18))*(1-exp(-700*(t-0.18))),0)",
        0.42, 160, 3900, 0.005, 0.3),
    "wonder_stage_complete": (
        "(0.3*sin(2*PI*(85*t+2.5*(1-exp(-35*t))))+0.13*(random(0)-0.5))*exp(-17*t)*(1-exp(-500*t))+if(gte(t,0.16),0.15*(sin(2*PI*329.63*(t-0.16))+0.16*sin(2*PI*659.26*(t-0.16)))*exp(-3*(t-0.16))*(1-exp(-80*(t-0.16))),0)+if(gte(t,0.32),0.15*sin(2*PI*493.88*(t-0.32))*exp(-3*(t-0.32))*(1-exp(-80*(t-0.32))),0)+if(gte(t,0.49),0.18*sin(2*PI*659.25*(t-0.49))*exp(-3*(t-0.49))*(1-exp(-80*(t-0.49))),0)",
        1.8, 40, 5000, 0.004, 1.5),
    "coins_gain": (
        "(1-exp(-2400*t))*(0.15*sin(2*PI*2780*t)*exp(-14*t)+0.12*sin(2*PI*4517*t)*exp(-21*t)+0.08*sin(2*PI*6323*t)*exp(-34*t)+0.055*sin(2*PI*8119*t)*exp(-55*t)+0.22*(random(0)-0.5)*exp(-220*t))+if(gte(t,0.095),(1-exp(-2400*(t-0.095)))*(0.13*sin(2*PI*3043*(t-0.095))*exp(-13*(t-0.095))+0.11*sin(2*PI*4931*(t-0.095))*exp(-20*(t-0.095))+0.075*sin(2*PI*6871*(t-0.095))*exp(-32*(t-0.095))+0.17*(random(1)-0.5)*exp(-200*(t-0.095))),0)",
        0.52, 1200, 10500, 0.0005, 0.4),
    "coins_spend": (
        "(1-exp(-2400*t))*(0.14*sin(2*PI*3079*t)*exp(-17*t)+0.12*sin(2*PI*5011*t)*exp(-25*t)+0.08*sin(2*PI*6977*t)*exp(-38*t)+0.2*(random(0)-0.5)*exp(-220*t))+if(gte(t,0.07),(1-exp(-2400*(t-0.07)))*(0.13*sin(2*PI*2617*(t-0.07))*exp(-18*(t-0.07))+0.105*sin(2*PI*4271*(t-0.07))*exp(-27*(t-0.07))+0.07*sin(2*PI*6101*(t-0.07))*exp(-42*(t-0.07))+0.18*(random(1)-0.5)*exp(-220*(t-0.07))),0)",
        0.43, 1100, 10500, 0.0005, 0.32),
    "trade_payment": (
        "0.12*(random(0)-0.5)*sin(PI*min(t/0.1,1))+if(gte(t,0.08),(1-exp(-2400*(t-0.08)))*(0.15*sin(2*PI*2879*(t-0.08))*exp(-14*(t-0.08))+0.12*sin(2*PI*4691*(t-0.08))*exp(-22*(t-0.08))+0.08*sin(2*PI*6563*(t-0.08))*exp(-34*(t-0.08))+0.2*(random(1)-0.5)*exp(-210*(t-0.08))),0)+if(gte(t,0.155),(1-exp(-2400*(t-0.155)))*(0.09*sin(2*PI*2543*(t-0.155))*exp(-19*(t-0.155))+0.07*sin(2*PI*4157*(t-0.155))*exp(-27*(t-0.155))+0.05*sin(2*PI*5839*(t-0.155))*exp(-40*(t-0.155))),0)",
        0.55, 700, 10500, 0.001, 0.42),
    "hand_move": (
        "0.34*(random(0)-0.5)*(0.5+0.5*sin(2*PI*27*t))*sin(PI*min(t/0.32,1))+if(gte(t,0.25),0.13*sin(2*PI*240*(t-0.25))*exp(-40*(t-0.25))*(1-exp(-650*(t-0.25))),0)",
        0.5, 450, 4200, 0.01, 0.37),
    "military_clash": (
        "(1-exp(-2600*t))*(0.115*sin(2*PI*1123*t+1.8*sin(2*PI*1739*t)*exp(-25*t))*exp(-6*t)+0.11*sin(2*PI*1877*t)*exp(-7*t)+0.095*sin(2*PI*3109*t)*exp(-10*t)+0.08*sin(2*PI*4621*t)*exp(-14*t)+0.065*sin(2*PI*6553*t)*exp(-20*t)+0.045*sin(2*PI*8693*t)*exp(-32*t)+0.38*(random(0)-0.5)*exp(-90*t))+if(gte(t,0.055),(1-exp(-2200*(t-0.055)))*(0.06*sin(2*PI*2381*(t-0.055))*exp(-10*(t-0.055))+0.045*sin(2*PI*3917*(t-0.055))*exp(-14*(t-0.055))+0.12*(random(1)-0.5)*exp(-120*(t-0.055))),0)",
        0.95, 650, 11000, 0.0005, 0.72),
    "military_victory": (
        notes(note(392, 0.18, decay=7, attack=65, harmonics=((2, 0.24), (3, 0.09))),
              note(523.25, 0.18, 0.16, 5, 65, ((2, 0.24), (3, 0.09)))),
        1.0, 90, 4200, 0.005, 0.8),
    "military_defeat": (
        notes(note(293.66, 0.15, decay=7, attack=60, harmonics=((2, 0.2),)),
              note(196, 0.16, 0.17, 5, 60, ((2, 0.2),))),
        1.0, 65, 3000, 0.005, 0.8),
    "military_tie": (
        notes(note(392, 0.15, decay=12, harmonics=((2, 0.15),)),
              note(392, 0.12, 0.13, 12, harmonics=((2, 0.15),))),
        0.52, 100, 3500, 0.003, 0.4),
    "age_start": (
        notes(note(293.66, 0.14, decay=3.5, attack=50, harmonics=((2, 0.18),)),
              note(440, 0.14, 0.15, 3.5, 50), note(587.33, 0.14, 0.3, 3.5, 50),
              note(880, 0.12, 0.45, 3.5, 50)),
        1.65, 75, 5000, 0.008, 1.35),
    "discard_choice_available": (
        "0.15*(random(0)-0.5)*sin(PI*min(t/0.16,1))+if(gte(t,0.14),0.22*(sin(2*PI*698.46*(t-0.14))+0.15*sin(2*PI*1396.92*(t-0.14)))*exp(-6*(t-0.14))*(1-exp(-160*(t-0.14))),0)+if(gte(t,0.32),0.22*sin(2*PI*1046.5*(t-0.32))*exp(-6*(t-0.32))*(1-exp(-160*(t-0.32))),0)",
        1.12, 160, 5500, 0.005, 0.92),
    "scoreboard_open": (
        "(1-exp(-1700*t))*(0.24*sin(2*PI*(175*t+0.55*(1-exp(-45*t))))*exp(-29*t)+0.09*sin(2*PI*337*t)*exp(-37*t)+0.6*(random(0)-0.5)*exp(-23*t)*(0.8+0.2*sin(2*PI*155*t))+0.12*(random(1)-0.5)*exp(-110*t))",
        0.3, 110, 9500, 0.0005, 0.21),
    "score_reveal": (
        "0.16*(sin(2*PI*1174.66*t)+0.17*sin(2*PI*2349.32*t))*exp(-15*t)*(1-exp(-350*t))+0.06*sin(2*PI*587.33*t)*exp(-10*t)*(1-exp(-200*t))",
        0.42, 160, 5000, 0.002, 0.32),
    "game_victory": (
        "0.22*sin(2*PI*(100*t+2*(1-exp(-25*t))))*exp(-15*t)*(1-exp(-350*t))+if(gte(t,0.12),0.15*(sin(2*PI*392*(t-0.12))+0.2*sin(2*PI*784*(t-0.12)))*exp(-3*(t-0.12))*(1-exp(-60*(t-0.12))),0)+if(gte(t,0.3),0.15*sin(2*PI*493.88*(t-0.3))*exp(-3*(t-0.3))*(1-exp(-60*(t-0.3))),0)+if(gte(t,0.48),0.17*(sin(2*PI*587.33*(t-0.48))+0.15*sin(2*PI*1174.66*(t-0.48)))*exp(-3*(t-0.48))*(1-exp(-60*(t-0.48))),0)+if(gte(t,0.7),0.17*sin(2*PI*783.99*(t-0.7))*exp(-2.7*(t-0.7))*(1-exp(-60*(t-0.7))),0)+if(gte(t,0.72),0.07*sin(2*PI*392*(t-0.72))*exp(-2.7*(t-0.72))*(1-exp(-60*(t-0.72))),0)",
        2.25, 50, 5500, 0.004, 1.9),
    "game_complete": (
        notes(note(523.25, 0.13, decay=3, attack=45),
              note(392, 0.13, 0.22, 3, 45),
              note(261.63, 0.14, 0.45, 2.7, 45, ((2, 0.2),)),
              note(329.63, 0.06, 0.45, 2.7, 45)),
        1.95, 65, 4000, 0.008, 1.6),
    "game_defeat": (
        notes(note(440, 0.12, decay=3.5, attack=40, harmonics=((2, 0.13),)),
              note(349.23, 0.12, 0.25, 3.5, 40),
              note(293.66, 0.13, 0.5, 3, 40, ((2, 0.12),))),
        1.85, 75, 3800, 0.008, 1.5),
}


def generate(name, directory):
    expression, duration, high, low, attack, fade_start = SOUNDS[name]
    filters = (
        f"highpass=f={high},lowpass=f={low},afade=t=in:d={attack},"
        f"afade=t=out:st={fade_start}:d={duration - fade_start:.6f}"
    )
    output = directory / f"{name}.wav"
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"aevalsrc='{expression}':s=48000:d={duration}",
        "-af", filters, "-c:a", "pcm_s16le", str(output),
    ], check=True)
    return output


def pcm(path):
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 48000):
            raise ValueError(f"Unexpected WAV format: {path}")
        return audio.readframes(audio.getnframes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sounds", nargs="*", help="Sound basenames (default: all)")
    parser.add_argument("--check", action="store_true", help="Compare regenerated PCM without editing assets")
    args = parser.parse_args()
    names = args.sounds or sorted(SOUNDS)
    unknown = set(names) - SOUNDS.keys()
    if unknown:
        parser.error(f"Unknown sounds: {', '.join(sorted(unknown))}")
    directory = pathlib.Path(__file__).resolve().parent / "wav"
    if args.check:
        with tempfile.TemporaryDirectory(prefix="seven-wonders-audio-") as temporary:
            for name in names:
                generated = generate(name, pathlib.Path(temporary))
                if pcm(generated) != pcm(directory / f"{name}.wav"):
                    raise ValueError(f"Regenerated PCM differs: {name}")
        print(f"Verified {len(names)} effects; temporary files removed.")
    else:
        directory.mkdir(exist_ok=True)
        for name in names:
            generate(name, directory)
        print(f"Generated {len(names)} effects in {directory}")


if __name__ == "__main__":
    main()
