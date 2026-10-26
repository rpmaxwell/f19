"""Paths and connection settings, from the environment.

  F19_DATA              data root: logs, plans, caches          default ~/f19-data
  F19_GAME_DIR          the game install (.WLD, .EXE, ...)       default ~/Documents/F-19 Stealth Fighter/MPS/F19
  F19_AUTOMATION_HOST   DOSBox-X automation socket host          default 127.0.0.1
  F19_AUTOMATION_PORT   DOSBox-X automation socket port          default 6000
  F19_DOSBOX_BIN        the DOSBox-X build with the automation socket   default ~/projects/dosbox-x/src/dosbox-x
  F19_DOSBOX_CONF       its config for F-19 (automation port, mounts)   default ~/Library/Preferences/f19-dosbox.conf

Layout under F19_DATA:
  logs/     nav, memlog, plan, missions, theater (cache), calib, radar_sites, ...
  saves/    DOSBox-X save-state slots and named checkpoints (checkpoint_<name>.sav)
  game/     derived game images (EGAME.bin, END.bin: unpacked load images)
  raw/      old one-off dumps;  attic/  things set aside, not deleted
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data: Path
    game_dir: Path
    host: str
    port: int
    dosbox_bin: Path = Path("~/projects/dosbox-x/src/dosbox-x").expanduser()
    dosbox_conf: Path = Path("~/Library/Preferences/f19-dosbox.conf").expanduser()

    @property
    def logs(self) -> Path: return self.data / "logs"

    @property
    def saves(self) -> Path: return self.data / "saves"

    @property
    def egame_bin(self) -> Path: return self.data / "game" / "EGAME.bin"

    @property
    def end_bin(self) -> Path: return self.data / "game" / "END.bin"

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        return cls(data=Path(env.get("F19_DATA", "~/f19-data")).expanduser(),
                   game_dir=Path(env.get("F19_GAME_DIR", "~/Documents/F-19 Stealth Fighter/MPS/F19")).expanduser(),
                   host=env.get("F19_AUTOMATION_HOST", "127.0.0.1"),
                   port=int(env.get("F19_AUTOMATION_PORT", 6000)),
                   dosbox_bin=Path(env.get("F19_DOSBOX_BIN", "~/projects/dosbox-x/src/dosbox-x")).expanduser(),
                   dosbox_conf=Path(env.get("F19_DOSBOX_CONF", "~/Library/Preferences/f19-dosbox.conf")).expanduser())


settings = Settings.from_env()
