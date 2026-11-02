import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeClient:
    """Mocked obsws ReqClient with the same snake_case method names."""

    def __init__(self, profiles=("Untitled",), current="Untitled", scenes=("Scene",), inputs=(), kinds=("macos-avcapture", "screen_capture", "coreaudio_input_capture")):
        self.profiles = list(profiles)
        self.current = current
        self.scenes = list(scenes)
        self.inputs = [{"inputName": n, "inputKind": k} for n, k in inputs]
        self.kinds = list(kinds)
        self.params = {}
        self.calls = []
        self.stream_active = False
        self.video_fail = False
        self.scene_items = {}   # scene -> list of {"sceneItemId","sourceName","sceneItemIndex"}
        self.transforms = {}    # (scene, id) -> dict
        self.devices = {"device": [{"itemName": "Logitech StreamCam", "itemValue": "cam1"}],
                        "device_id": [{"itemName": "Logitech StreamCam", "itemValue": "m1"}]}
        self.settings = {}
        self.muted = {}
        self.current_scene = None
        self.next_id = 1
        self.source_size = (1280, 720)
        self.zero_polls = 0

    def _c(self, name, *a):
        self.calls.append((name, *a))

    def get_version(self):
        return NS(obs_version="33.0.1", obs_web_socket_version="5.6.0")

    def get_profile_list(self):
        return NS(current_profile_name=self.current, profiles=list(self.profiles))

    def create_profile(self, name):
        self._c("create_profile", name)
        self.profiles.append(name)
        self.current = name
        self._mkdir(name)

    def _mkdir(self, name):
        from obsagent_lib import paths
        d = paths.profiles_dir() / "".join(c for c in name if c.isalnum())
        d.mkdir(parents=True, exist_ok=True)
        (d / "basic.ini").write_text(f"[General]\nName={name}\n")

    def set_current_profile(self, name):
        self._c("set_current_profile", name)
        self.current = name

    def get_profile_parameter(self, cat, name):
        return NS(parameter_value=self.params.get((cat, name)), default_parameter_value=None)

    def set_profile_parameter(self, cat, name, value):
        self._c("set_profile_parameter", cat, name, value)
        self.params[(cat, name)] = value

    def set_video_settings(self, *a):
        self._c("set_video_settings", *a)
        if self.video_fail:
            raise RuntimeError("output active")

    def get_video_settings(self):
        return NS(base_width=1920, base_height=1080, output_width=1920, output_height=1080, fps_numerator=60, fps_denominator=1)

    def get_scene_list(self):
        return NS(current_program_scene_name=self.scenes[0] if self.scenes else None,
                  scenes=[{"sceneName": s} for s in self.scenes])

    def create_scene(self, name):
        self._c("create_scene", name)
        self.scenes.append(name)

    def get_input_list(self, kind=None):
        return NS(inputs=list(self.inputs))

    def get_input_kind_list(self, unversioned=False):
        return NS(input_kinds=list(self.kinds))

    def _add_item(self, scene, name):
        iid = self.next_id
        self.next_id += 1
        lst = self.scene_items.setdefault(scene, [])
        lst.append({"sceneItemId": iid, "sourceName": name, "sceneItemIndex": len(lst)})
        w, h = self.source_size
        self.transforms[(scene, iid)] = {"sourceWidth": 0 if self.zero_polls else w,
                                         "sourceHeight": 0 if self.zero_polls else h}
        return iid

    def create_input(self, scene, name, kind, settings, enabled):
        self._c("create_input", scene, name, kind)
        self.inputs.append({"inputName": name, "inputKind": kind})
        self.settings[name] = dict(settings or {})
        return NS(scene_item_id=self._add_item(scene, name))

    def remove_input(self, name):
        self._c("remove_input", name)
        self.inputs = [i for i in self.inputs if i["inputName"] != name]
        for sc, lst in self.scene_items.items():
            self.scene_items[sc] = [i for i in lst if i["sourceName"] != name]

    def set_input_name(self, name, new_name):
        self._c("set_input_name", name, new_name)
        for i in self.inputs:
            if i["inputName"] == name:
                i["inputName"] = new_name
        for lst in self.scene_items.values():
            for i in lst:
                if i["sourceName"] == name:
                    i["sourceName"] = new_name

    def remove_scene_item(self, scene, item_id):
        self._c("remove_scene_item", scene, item_id)
        self.scene_items[scene] = [i for i in self.scene_items.get(scene, [])
                                   if i["sceneItemId"] != item_id]

    def create_scene_item(self, scene, source, enabled=None):
        self._c("create_scene_item", scene, source)
        return NS(scene_item_id=self._add_item(scene, source))

    def get_scene_item_list(self, scene):
        return NS(scene_items=list(self.scene_items.get(scene, [])))

    def get_scene_item_transform(self, scene, iid):
        self._c("get_scene_item_transform", scene, iid)
        t = self.transforms[(scene, iid)]
        if self.zero_polls > 0:
            self.zero_polls -= 1
            if self.zero_polls == 0:
                t["sourceWidth"], t["sourceHeight"] = self.source_size
        return NS(scene_item_transform=dict(t))

    def set_scene_item_transform(self, scene, iid, tr):
        self._c("set_scene_item_transform", scene, iid, tr)
        self.transforms[(scene, iid)].update(tr)

    def set_scene_item_index(self, scene, iid, idx):
        self._c("set_scene_item_index", scene, iid, idx)
        for it in self.scene_items[scene]:
            if it["sceneItemId"] == iid:
                it["sceneItemIndex"] = idx

    def set_scene_item_locked(self, scene, iid, locked):
        self._c("set_scene_item_locked", scene, iid, locked)
        self.transforms.setdefault((scene, iid), {})["locked"] = locked

    def get_input_properties_list_property_items(self, name, prop):
        self._c("get_input_properties_list_property_items", name, prop)
        return NS(property_items=list(self.devices[prop]))

    def set_input_settings(self, name, settings, overlay):
        self._c("set_input_settings", name, settings)
        self.settings.setdefault(name, {}).update(settings)

    def get_input_settings(self, name):
        return NS(input_settings=dict(self.settings.get(name, {})))

    def set_input_mute(self, name, muted):
        self._c("set_input_mute", name, muted)
        self.muted[name] = muted

    def get_input_mute(self, name):
        return NS(input_muted=self.muted.get(name, False))

    def set_current_program_scene(self, name):
        self._c("set_current_program_scene", name)
        self.current_scene = name

    def set_stream_service_settings(self, t, s):
        self._c("set_stream_service_settings", t, s)

    def start_stream(self):
        self._c("start_stream")

    def stop_stream(self):
        self._c("stop_stream")

    def start_record(self):
        self._c("start_record")

    def stop_record(self):
        self._c("stop_record")

    def get_stats(self):
        return NS(cpu_usage=12.0, memory_usage=400.0, available_disk_space=1e5, active_fps=60.0,
                  average_frame_render_time=2.0, render_skipped_frames=0, render_total_frames=1000,
                  output_skipped_frames=0, output_total_frames=1000)

    def get_stream_status(self):
        return NS(output_active=self.stream_active, output_reconnecting=False, output_timecode="00:00:01",
                  output_duration=1000, output_congestion=0.0, output_bytes=1000, output_skipped_frames=0,
                  output_total_frames=100)

    def get_record_status(self):
        return NS(output_active=False, output_paused=False, output_timecode="00:00:00", output_bytes=0)


@pytest.fixture
def fake():
    return FakeClient()


@pytest.fixture
def obs_dir(tmp_path, monkeypatch):
    d = tmp_path / "obs-studio"
    monkeypatch.setenv("OBSAGENT_OBS_DIR", str(d))
    monkeypatch.setenv("OBSAGENT_CONFIG_DIR", str(tmp_path / "cfg"))
    from obsagent_lib import paths
    monkeypatch.setattr(paths, "obs_running", lambda: False)
    monkeypatch.setattr(paths, "keychain_get", lambda acct: None)
    return d
