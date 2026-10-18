"""Immutable official D3 family identities and per-variant native geometry."""

from dataclasses import dataclass
import math
from .d3_assets import ASSETS as EDGE_ASSETS


@dataclass(frozen=True)
class D3Profile:
    backend: str
    model: str
    revision: str
    assets: dict
    weights: dict
    readout_sha256: str
    readout_bytes: int
    hidden: int
    layers: int
    heads: int

    @property
    def label(self):
        return {
            "d3-edge": "D3 Edge",
            "d3-lite": "D3 Lite",
            "d3-nano": "D3 Nano",
            "d3-mini": "D3 Mini",
            "d3-flash": "D3 Flash",
            "d3": "D3",
        }[self.backend]

    @property
    def fields(self):
        return {
            "backend": self.backend,
            "model": self.model,
            "revision": self.revision,
            "training_method": "native-readout",
        }

    @property
    def weight_bytes(self):
        return sum(item["bytes"] for item in self.weights.values())

    @property
    def trainable_parameters(self):
        return 255 * self.hidden

    @property
    def cpu_gib(self):
        return (
            3
            if self.backend == "d3-edge"
            else math.ceil(2 * self.weight_bytes / 1024**3) + 2
        )

    @property
    def cuda_gib(self):
        return (
            2
            if self.backend == "d3-edge"
            else math.ceil(self.weight_bytes / 1024**3) + 1
        )


# Source: each model's immutable Hugging Face API/config/LFS inventory.
# Keep variant-specific source hashes, shard layouts and readouts distinct.
_PINS = {
    "d3-edge": {
        "model": "vllm-sr/d3-edge",
        "revision": "c454ee39b862248d70abd7f84195d2f54ffe6d43",
        "weights": {
            "model-00001-of-00001.safetensors": {
                "bytes": 1188677968,
                "sha256": "91bb8618450a7f628f1432bbced76bc19ee7346c93d191a228fdd33c25a57a9d",
            }
        },
        "readout_sha256": "de33d5dc795bbd77168e40bfadc340cca9b9f163b88744c10e1a788f4e166d3d",
        "readout_bytes": 1044560,
        "hidden": 1024,
        "layers": 12,
        "heads": 8,
        "asset_overrides": {},
        "omitted_assets": [],
    },
    "d3-lite": {
        "model": "vllm-sr/d3-lite",
        "revision": "03560c26f5199c3c04129a2ccb38960c913635e0",
        "weights": {
            "model-00001-of-00001.safetensors": {
                "bytes": 1706027688,
                "sha256": "ca6222dbf2b72899ae45580ed84d26d1f0e69a8de212edda295072000e34a4c2",
            }
        },
        "readout_sha256": "fe11cfac5c79b6a83e35b707581b1260214279bb22b003c215c62719186ef629",
        "readout_bytes": 1044560,
        "hidden": 1024,
        "layers": 24,
        "heads": 8,
        "asset_overrides": {
            "config.json": "a9071195384e1fc2c68f9fe0fae007a22182ab77eb14fe8228a1a24b37d3cee9",
            "model.safetensors.index.json": "d2ff81f32fc8ad51c20e3b76abf1689da6246280dad39be981567c7b4367f668",
            "NOTICE": "e7fecd68cfb3c894924d48c642230cd2b96d3cf03ecc13907f5bcc55d52f3a9f",
        },
        "omitted_assets": [],
    },
    "d3-nano": {
        "model": "vllm-sr/d3-nano",
        "revision": "462efc366229ea268cf4eed3738b3fc4415e4817",
        "weights": {
            "model.safetensors": {
                "bytes": 4426555160,
                "sha256": "7269b3260f1106f3443bfdb028a76143be2e679db3f29a31f4dfd1d595f03155",
            }
        },
        "readout_sha256": "662e8a26d8016b2d479f1ca66bf64944603316c2b5e074ab7ac3304b8ae045e9",
        "readout_bytes": 2089040,
        "hidden": 2048,
        "layers": 24,
        "heads": 8,
        "asset_overrides": {
            "config.json": "9caa6ab8956c859c3852328dda6322893a18b30826d3916db91d938d6c74ac4f",
            "decision_config.json": "9175a0f42d5886def9c8429fdf8b0be58a3c97d106b94520e2ac38fe326302bf",
            "NOTICE": "300b6105c41a1843653e40be644c3d54e06288b25199373e04143530cc3db366",
        },
        "omitted_assets": ["model.safetensors.index.json"],
    },
    "d3-mini": {
        "model": "vllm-sr/d3-mini",
        "revision": "bd0090dd758d32ba59ef5c749910a8e87fc4391f",
        "weights": {
            "model-00001-of-00002.safetensors": {
                "bytes": 4973332144,
                "sha256": "60621cb8c3f90e634b98f0ffaf609159ee2c450630386f09dd5f7fd7938d7598",
            },
            "model-00002-of-00002.safetensors": {
                "bytes": 4105283936,
                "sha256": "6189569fbe9245030d7ffb0b60a52268b7386e7a85a57aee1274a5178c1cc53d",
            },
        },
        "readout_sha256": "d570665c1da72cb9fca7ad7b23a25720eb691362694dd69d4c80c17d7656ed55",
        "readout_bytes": 2611280,
        "hidden": 2560,
        "layers": 32,
        "heads": 16,
        "asset_overrides": {
            "config.json": "8f5f404a71ecc53c4ff26fc42cb4c778ffacfb4c9623f5e2d51245166d6b5057",
            "decision_config.json": "588bc0ac7644479b9adde0498d80b6fb7c576388992564ac67599c05d29add07",
            "tokenizer_config.json": "316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8",
            "chat_template.jinja": "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715",
            "model.safetensors.index.json": "613cc71f6441a964b0a87470536f8fb0c639bdd86b0362027c1c2b1a6e0a4a8c",
            "NOTICE": "1b1c0d9508ca9703a5f7eabfc70800676e167ad1de1e72ac69f4b9cab1b9272b",
        },
        "omitted_assets": [],
    },
    "d3-flash": {
        "model": "vllm-sr/d3-flash",
        "revision": "13dc1cc8ef23caefa765907e1d764751e5629f7d",
        "weights": {
            "model-00001-of-00004.safetensors": {
                "bytes": 4911663824,
                "sha256": "2c322016b91b1232c0d7628560164560d06d477a3edc3362714635f0e2f5949c",
            },
            "model-00002-of-00004.safetensors": {
                "bytes": 4987839568,
                "sha256": "f769650fb747d85132fb321335af7c71fcf3ba95fd1b1e806447e891cb4b0546",
            },
            "model-00003-of-00004.safetensors": {
                "bytes": 4954726768,
                "sha256": "c85a0f2f8df221b7864c74e778d802dc22fe1237fe2fa6ea8b81d7b612800a33",
            },
            "model-00004-of-00004.safetensors": {
                "bytes": 1931248816,
                "sha256": "d686ef3f6cdeeaad5eedde9d516c29ed1c90a1a322dd002765732869c11fa269",
            },
        },
        "readout_sha256": "8bbc9a9df4e1b7df85a80a1091977092b938d8d2d2869dfaeb8fd36063cf322f",
        "readout_bytes": 4178000,
        "hidden": 4096,
        "layers": 32,
        "heads": 16,
        "asset_overrides": {
            "config.json": "9c3e6eea217964096c7b032285bab6757720a847fbb9d0a04e551d2b1606c0eb",
            "decision_config.json": "0269c72ac055d8149a7a778c477780af361f4c1cde3fd0e5ba9af791453ece18",
            "tokenizer_config.json": "316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8",
            "chat_template.jinja": "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715",
            "model.safetensors.index.json": "9ddd9f9ce431ff771d9b37b354ba11aa1b74c18d87c5a48c964da794bd1029ee",
            "NOTICE": "ae7ffb4d788c076d655394f0f116ad4b7de54e3bca9b655df5c669ea5bae74f6",
        },
        "omitted_assets": [],
    },
    "d3": {
        "model": "vllm-sr/d3",
        "revision": "cf15549a78fc52598ddd1951f321c58372bbd64f",
        "weights": {
            "model-00001-of-00011.safetensors": {
                "bytes": 4997471976,
                "sha256": "4ecb29a7d1e2d1d0ad97008e5405a11d510a8a712a0b6491928359176fd56029",
            },
            "model-00002-of-00011.safetensors": {
                "bytes": 4965144568,
                "sha256": "41dc277e43c9c5734656807da9f856f932338125a68ca6ec16b71dbdd6f97d62",
            },
            "model-00003-of-00011.safetensors": {
                "bytes": 4933789248,
                "sha256": "46959de0f2202f7d6c53d122abff46c5f49f53871844eee01d60f4c035080146",
            },
            "model-00004-of-00011.safetensors": {
                "bytes": 4965227496,
                "sha256": "946e87b8fa934091dc942622e5b72d16b8eb4d4571cafb2dbe36cb1022ea9275",
            },
            "model-00005-of-00011.safetensors": {
                "bytes": 4974750552,
                "sha256": "87c7ce2751c7bf147394aa0413ae5e509907d52e16f59adb4a2db18ee023cef7",
            },
            "model-00006-of-00011.safetensors": {
                "bytes": 4924266240,
                "sha256": "e4da6d081a7ef2012413537ef0e9cf6872df07b395cb65c08c05f9f79abb974a",
            },
            "model-00007-of-00011.safetensors": {
                "bytes": 4974750560,
                "sha256": "262d7506f49ff1596aff610dccd46f4a33d6d9dab386bc5795c5abcab9a4d683",
            },
            "model-00008-of-00011.safetensors": {
                "bytes": 4902229944,
                "sha256": "9e29bd831f8582119505018aeb3047030b698fe71fdd63bb38de2622b85534ed",
            },
            "model-00009-of-00011.safetensors": {
                "bytes": 4996786840,
                "sha256": "0f1e3dfda5583dd38d61da22145451c8507ff7858a4293aa1e0e48ff20f9c319",
            },
            "model-00010-of-00011.safetensors": {
                "bytes": 4902229960,
                "sha256": "f7a650a697b92b6561355624ae89b1aba2fc7df01572ff92a6531fa1c79cca14",
            },
            "model-00011-of-00011.safetensors": {
                "bytes": 2634155256,
                "sha256": "9842116c181985fec72fcb9560f5c9b849800f6322f8304dcc466990758c7355",
            },
        },
        "readout_sha256": "5319cbf3ccdc5d3ba348b318d7b54acfc3a861d3ff94b578e787852c8726055a",
        "readout_bytes": 5222480,
        "hidden": 5120,
        "layers": 64,
        "heads": 24,
        "asset_overrides": {
            "d3_runtime.py": "2d90e93ac06b9ca4a84e0610ab9acfa2d6793ea4b40eec1b4a3da47577bdea37",
            "config.json": "7e16284fafd2d54b73c10073c0391bfd6804886ceaf2cbe37fdb62d4c6813dea",
            "decision_config.json": "6b80ca11bd6ba481df4b3c187db8a5d1983786563ca05e2edccb0bdac0d2fc4f",
            "tokenizer.json": "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3",
            "tokenizer_config.json": "b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27",
            "chat_template.jinja": "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041",
            "model.safetensors.index.json": "dc39d771cb240f775399e1845b11d081d8e007f14eecae3b780bc6272b128a46",
            "NOTICE": "61f4ea488e1fed0c3fce97171506a23152c2591116442f78f15827fb9e6af469",
        },
        "omitted_assets": [],
    },
}

_PROFILES = {}
for _backend, _value in _PINS.items():
    _data = dict(_value)
    _overrides = _data.pop("asset_overrides")
    _omitted = _data.pop("omitted_assets")
    _assets = (
        EDGE_ASSETS
        if _backend == "d3-edge"
        else {
            **{k: v for k, v in EDGE_ASSETS.items() if k not in _omitted},
            **_overrides,
        }
    )
    _PROFILES[_backend] = D3Profile(backend=_backend, assets=_assets, **_data)
BACKENDS = tuple(_PROFILES)


def get_profile(backend="d3-edge"):
    if not isinstance(backend, str) or backend not in _PROFILES:
        raise ValueError("Choose a pinned D3 family backend")
    return _PROFILES[backend]
