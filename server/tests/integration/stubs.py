"""Contract-shaped test doubles. Never imported by production."""
import re
from server.contracts import (
    AircraftPublic, CaptionMsg, StateMsg, WorldSnapshot,
)


class World:
    def __init__(self, bus, data_dir):
        self.t = 0
        self.heading = 75
        self.target = None

    def load_scenario(self, name, seed):
        self.t = 0
        self.heading = 75

    def configure(self, settings):
        pass

    def tick(self, dt):
        self.t += dt
        if self.target is not None:
            self.heading = self.target

    def public_state(self):
        aircraft = AircraftPublic(
            callsign="BAW117", spoken_plain="British Airways 117", actype="A320",
            wake="M", flow="ARR", phase="VECTORS", x=0, y=-10, lat=51, lon=0,
            alt=8000, vs=0, hdg=self.heading, track=self.heading, ias=220, gs=220,
            cleared_alt=8000, established=False,
        )
        return StateMsg(t=self.t, paused=False, sim_speed=1, runway="26L",
                        aircraft=[aircraft], alerts=[])

    def snapshot(self):
        return WorldSnapshot(t=self.t, aircraft={}, alerts=[])


class Pilot:
    def __init__(self, world, bus, data_dir):
        pass

    def set_error_rate(self, rate):
        pass

    def tick(self, t):
        pass

    def on_sim_event(self, event):
        return None


class Rules:
    def __init__(self, world, bus, data_dir):
        pass

    def on_event(self, event):
        pass

    def on_tick(self, snapshot):
        pass


class Composer:
    def __init__(self, data_dir):
        pass


class Voice:
    def __init__(self, world, pilot, composer, bus, hub):
        self.world, self.hub = world, hub
        self.ptt = []

    async def on_typed(self, text):
        heading = int(re.search(r"heading (\d{3})", text)[1])
        await self.hub.send(CaptionMsg(
            tx_id="fixture", speaker="BAW117", stage="pilot_text",
            text=f"Left heading {heading}, Speedbird one one seven",
        ))
        self.world.target = heading

    async def on_ptt(self, state):
        self.ptt.append(state)

    async def on_audio_in(self, pcm):
        pass


class Narrative:
    pass


FACTORIES = dict(world=World, pilot=Pilot, rules=Rules, composer=Composer,
                 voice=Voice, narrative=Narrative)
