"""Publisher asset/weight identities for the native Perplexity v1.1 profile.

From the pinned publisher release-manifest and Hub LFS metadata. No weight files
are distributed here. See docs/models/perplexity-native.md and THIRD_PARTY_NOTICES.md.
"""

from .perplexity_assets import MODEL, REVISION, BASE_MODEL, BASE_REVISION

ASSETS = {
    "LICENSE": {
        "bytes": 11544,
        "sha256": "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a",
    },
    "NOTICE": {
        "bytes": 604,
        "sha256": "3792cbe9e964a7ed65fff010d3f94bb27c6e726e2940ab6b0e1db5d30df92510",
    },
    "chat_template.jinja": {
        "bytes": 8952,
        "sha256": "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041",
    },
    "config.json": {
        "bytes": 3757,
        "sha256": "687f20bf493b8ee787dadaf338cf6df644dad44e32bcd2c3677a47c6c213d8b3",
    },
    "decision_config.json": {
        "bytes": 6715,
        "sha256": "68d4a4c704a0c42837f749b0287bd80f70d186ed3be4dc6a90802e78223925e8",
    },
    "model.safetensors.index.json": {
        "bytes": 103954,
        "sha256": "dc39d771cb240f775399e1845b11d081d8e007f14eecae3b780bc6272b128a46",
    },
    "processor_config.json": {
        "bytes": 1219,
        "sha256": "f76f21cad63dc9da928676e84e4f4889f154b9122e8c3f6b38093d39729fc266",
    },
    "requirements.txt": {
        "bytes": 314,
        "sha256": "60ce3c0ba6ec117b114e2ec1641baef7f50cb68020fc9bf46653a86e885e32e8",
    },
    "source/pyproject.toml": {
        "bytes": 1462,
        "sha256": "e4d530c6a9589ebb719571582bf910cb6cd7dfd81235b88d5dd30398a083d2fd",
    },
    "source/src/autojev/__init__.py": {
        "bytes": 0,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    },
    "source/src/autojev/data.py": {
        "bytes": 34479,
        "sha256": "afa8178c12a25ef3490f5b6691fc045de2aaa4dd7a204325cd8e0cd0f190e000",
    },
    "source/src/autojev/distributed.py": {
        "bytes": 3138,
        "sha256": "d8e5bf5c0e97e29d348bc2169f283eba10d3487f5c7c102949e2f6c249c74951",
    },
    "source/src/autojev/evaluate.py": {
        "bytes": 26697,
        "sha256": "f293a50320ab6573ca18bce28290f1a6914679c6f6de540644c9f09ca79dd2fd",
    },
    "source/src/autojev/events.py": {
        "bytes": 756,
        "sha256": "72315600ac072a73f70f7ed92ec62a79c91baacd128e6da0591549ff17b3f911",
    },
    "source/src/autojev/loss.py": {
        "bytes": 638,
        "sha256": "2e4f4ae4b8aafac0247f931220b592a140bd2f5cd032dce8a517a158fbeedda3",
    },
    "source/src/autojev/model.py": {
        "bytes": 17304,
        "sha256": "02940963e9fba5736b8f625f860812f31de2e6658a49606fbed49fd7215c20f9",
    },
    "source/src/autojev/optim.py": {
        "bytes": 4702,
        "sha256": "68b1b30c7fce460245a613b9154b23c1fd2659af0327f9f5cfa2efe1b68ef147",
    },
    "source/src/autojev/report.py": {
        "bytes": 49671,
        "sha256": "158edec55116e4b7801df5d23c08c5f4a754be566012d0cae80d401380c5c563",
    },
    "source/src/autojev/server.py": {
        "bytes": 8698,
        "sha256": "840088936dbcd19c9c1a0503f348fdc3e5aa07b7b58c690eaccc9dda314691c1",
    },
    "source/src/autojev/sft_numeric.py": {
        "bytes": 17397,
        "sha256": "677a2123a33d6e90343ababf43c844e98b235abf558fc1bda36a6b0b146c1b21",
    },
    "source/src/autojev/sft_pipeline.py": {
        "bytes": 33777,
        "sha256": "248830ae7b49b4000354a2fd8ea19ce0f14d131fdd89124db38b7f882d24e8dd",
    },
    "source/src/autojev/sft_synthetic.py": {
        "bytes": 53816,
        "sha256": "3d0e520a5e901605dcbc89663a35f7ada93204cef3dd50946f0d307db8846764",
    },
    "source/src/autojev/synthetic.py": {
        "bytes": 21916,
        "sha256": "e285155412372d55e07f2199c040b5e9dd8c0b716ad9b4fd907f06149be38633",
    },
    "source/src/autojev/train.py": {
        "bytes": 49282,
        "sha256": "33b7c1447abbfd0ed42f5610c6752b5ff7e2427ab4dd576a0b39a696581f0748",
    },
    "source/src/autojev/types.py": {
        "bytes": 1800,
        "sha256": "250ba2eea1cd9bc5501bc76ba76b2f4a1d6c692a7fb1bba4b01affff38cb81ca",
    },
    "source/uv.lock": {
        "bytes": 696353,
        "sha256": "cba79e0f2caa23d14740ce0008076aadd5dd739f59a5fdf9eaaf1943fe207fc0",
    },
    "tokenizer.json": {
        "bytes": 19989492,
        "sha256": "6f32ce20dc35f57a7f9ad1eac03525bd7d30f9df8cea6507e958279cc3657706",
    },
    "tokenizer_config.json": {
        "bytes": 1165,
        "sha256": "09564f0b57b433b55d536630be3044b4183dbe04d0cbc7b1e9b1154db3671b0b",
    },
}

WEIGHTS = {
    "model-00001-of-00011.safetensors": {
        "bytes": 4997471976,
        "sha256": "871e1db3d5eac13577467b25e5578e51de94fd7401e47f4a8222db62e7462ef6",
    },
    "model-00002-of-00011.safetensors": {
        "bytes": 4965144568,
        "sha256": "648be02d3d6581858a136a30f644e2cdc714d4f7bc2e23c64d630d30f3bf549e",
    },
    "model-00003-of-00011.safetensors": {
        "bytes": 4933789248,
        "sha256": "8b070e475ad9ecbe3d74a7107589e1d763b131dad83e10a3a7292d434b6dc4ff",
    },
    "model-00004-of-00011.safetensors": {
        "bytes": 4965227496,
        "sha256": "4a9fe94648fc385ab5f21c243743c9284f77104c3447659e6cfcab50b16fb15e",
    },
    "model-00005-of-00011.safetensors": {
        "bytes": 4974750552,
        "sha256": "715cc1ea4a9336759ed6dd026b9d3ea886cf4597f9437cdd55180d290e16fe69",
    },
    "model-00006-of-00011.safetensors": {
        "bytes": 4924266240,
        "sha256": "f6fce9ec95eb4a46a7e74852b996f52b6b0d6924f24070590b05bec7c4b6d7c3",
    },
    "model-00007-of-00011.safetensors": {
        "bytes": 4974750560,
        "sha256": "cd407127147c1b08c47003446d43f4608704c1cb53b9de648b7bc2f8a2a352d4",
    },
    "model-00008-of-00011.safetensors": {
        "bytes": 4902229944,
        "sha256": "e3c361f9dee455590897d92c00d10a317363845b008e8e66de4d12ea0d6d3319",
    },
    "model-00009-of-00011.safetensors": {
        "bytes": 4996786840,
        "sha256": "7c4ec1d3435a341041cd1406866aab7f5780a0cd6ca9581adb4de6fafb52ef9c",
    },
    "model-00010-of-00011.safetensors": {
        "bytes": 4902229960,
        "sha256": "a05b9bd813536e3a945e6dd48403fcffb0a1d34ab7942f2adbaeb99402c4a809",
    },
    "model-00011-of-00011.safetensors": {
        "bytes": 2634155256,
        "sha256": "cdd6261ab7c785908c010ff96d0094660681024c265356bc01d528fbbbb5394f",
    },
    "readout.safetensors": {
        "bytes": 2611288,
        "sha256": "774eadedb750f68d38cd7d0d7311a646dc9aed00636910e298951b5d78f16e49",
    },
}

PARAMETERS = 26085330160
WEIGHT_BYTES = 52173413928
