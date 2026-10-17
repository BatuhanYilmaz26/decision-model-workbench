"""Pinned official D3 Edge assets; independent variants need their own profile."""

MODEL = "vllm-sr/d3-edge"
REVISION = "c454ee39b862248d70abd7f84195d2f54ffe6d43"
WEIGHTS = "model-00001-of-00001.safetensors"
BASE_WEIGHTS = "91bb8618450a7f628f1432bbced76bc19ee7346c93d191a228fdd33c25a57a9d"
BASE_READOUT = "de33d5dc795bbd77168e40bfadc340cca9b9f163b88744c10e1a788f4e166d3d"
ASSETS = {
    "modeling_d3.py": "f8f8db23ff58f1a990ffa24977d744a50e1b4446d02a64c22070967705b6dbef",
    "pipeline_d3.py": "4f9297c7c74e596e9b67df91a1e6823cfada48bf781336f141974ff6a2f1038e",
    "d3_runtime.py": "adf7f7aa085674b5682e492d8b2e3cc4f11c67b0d153515c742e907e9b550917",
    "d3_format.py": "3badfbc70561bb3f5b8ec16a11d2c455b96ddb32caace0a8a54ca102eae1f3fa",
    "d3_fast.py": "68a1fb9a2c3bde9ac2763bf504decae592ca12722698fbde1b39a31262955535",
    "d3_kernels.py": "8f7534ca1e1846ebf7c4c73058002f3be4dbf0d5d426f0815150ae0cbe7677bd",
    "config.json": "5e529d9aea2f8d387750acf5b02cc32a47d7e87dc65d6d008fba98e5d15acc4d",
    "decision_config.json": "30b6095b945d5a93697868afdf9be9123ea892085fbaff3078112c48da20dc6b",
    "tokenizer.json": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
    "tokenizer_config.json": "49e2b6e395f959f077f1e992b338919c0d4a9732fc6e613995e06557f843500c",
    "chat_template.jinja": "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80",
    "merges.txt": "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d",
    "vocab.json": "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003",
    "preprocessor_config.json": "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516",
    "video_preprocessor_config.json": "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13",
    "model.safetensors.index.json": "7d3951baba643a2c4fe2263d8602ef37b1c1f804b58949a5469449da6a588b03",
    "LICENSE": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    "NOTICE": "0e7694dee1927d83b3637be7a8f989b941ebd884dd40767b995c7adee484789b",
}
