import argparse
from pathlib import Path
from src.pipeline import Pipeline
from src.io import write_outputs

p = argparse.ArgumentParser(description="Process one epicrisis and write result/score/context JSON files.")
p.add_argument("file")
p.add_argument("--output", default="./out")
p.add_argument("--config", default=None)
a = p.parse_args()
path = Path(a.file)
pipe = Pipeline(a.config)
pack = pipe.run(path.read_text(encoding="utf-8"), path.name)
paths = write_outputs(pack, a.output, path.stem)
print(paths["result"])
print(paths["score"])
print(paths["context"])
