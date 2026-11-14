#!/usr/bin/env python
"""jarvis-show: ativa o OpenJarvis com duas palmas.

Escuta o microfone, e ao detectar duas palmas seguidas toca o jingle de
abertura (se existir), abre a interface web e inicia o chat por voz.

Ajustes (variaveis de ambiente):
  JARVIS_CLAP_THRESH  Sensibilidade da palma (padrao 0.35).
                       Maior = menos sensivel, menor = mais sensivel.
  JARVIS_MIC           Qual microfone usar: indice numerico (ex: "1")
                       ou parte do nome do dispositivo (ex: "Realtek").
"""
import os
import sys
import time
import threading
import subprocess
import webbrowser
from pathlib import Path

import numpy as np
import sounddevice as sd

REPO_ROOT = Path(__file__).resolve().parent
JINGLE = REPO_ROOT / "voz" / "assets" / "jingle.mp3"
WEB_URL = "http://127.0.0.1:8000"

CLAP_THRESH = float(os.environ.get("JARVIS_CLAP_THRESH", "0.35"))
MIC = os.environ.get("JARVIS_MIC", "")
SAMPLE_RATE = 16000
BLOCK_MS = 30
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_MS / 1000)
CLAP_WINDOW_S = 1.2   # janela maxima entre as duas palmas
REFRACTORY_S = 0.25   # tempo minimo entre deteccoes (ignora o eco da mesma palma)


def resolve_device():
    if not MIC:
        return None
    try:
        return int(MIC)
    except ValueError:
        pass
    for idx, dev in enumerate(sd.query_devices()):
        if MIC.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
            return idx
    print(f"Aviso: microfone '{MIC}' nao encontrado, usando o padrao do sistema.")
    return None


def jarvis_exe():
    candidate = Path(sys.executable).with_name("jarvis.exe")
    if candidate.exists():
        return str(candidate)
    return "jarvis"


def play_jingle():
    if JINGLE.exists():
        try:
            os.startfile(str(JINGLE))
        except OSError as exc:
            print(f"Nao consegui tocar o jingle: {exc}")


def launch_jarvis():
    exe = jarvis_exe()
    print("\nDuas palmas detectadas! Abrindo o Jarvis...")
    play_jingle()
    subprocess.Popen([exe, "start"])
    time.sleep(2)
    try:
        webbrowser.open(WEB_URL)
    except Exception:
        pass
    subprocess.run([exe, "chat", "--voice", "--hands-free"])


def listen_for_claps():
    device = resolve_device()
    detected = threading.Event()
    state = {"times": [], "last": 0.0}

    def callback(indata, frames, time_info, status):
        now = time.monotonic()
        peak = float(np.max(np.abs(indata)))
        if peak < CLAP_THRESH or now - state["last"] < REFRACTORY_S:
            return
        state["last"] = now
        state["times"] = [t for t in state["times"] if now - t <= CLAP_WINDOW_S] + [now]
        if len(state["times"]) >= 2:
            detected.set()

    print(f"Escutando palmas... (limiar={CLAP_THRESH}, Ctrl+C para sair)")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SIZE,
        channels=1,
        dtype="float32",
        device=device,
        callback=callback,
    ):
        detected.wait()


def main():
    try:
        while True:
            listen_for_claps()
            launch_jarvis()
            print("\nSessao encerrada. Bata palma 2x de novo para reabrir (Ctrl+C para sair).")
    except KeyboardInterrupt:
        print("\nAte mais!")


if __name__ == "__main__":
    main()
