"""Reproduce the catalog prompt studies without modifying application memory."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random
import re
from statistics import mean
import subprocess
import sys
from zlib import crc32

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "legacy"))

from ai_modeler.meta.evaluate import (
    DATASET_PATH,
    _split_by_family,
    evaluate_holdout,
    evaluate_incremental,
    evaluate_incremental_hard,
    evaluate_variant_ablation,
    load_dataset,
)
from ai_modeler.meta.features import build_vocab, embed_many, normalize, numeric_tokens
from ai_modeler.meta.memory import MetaMemory


CONFIDENCE_THRESHOLD = 0.55  # Legacy UI's existing hint gate.


def model_key(text):
    return re.sub(r"[\s_\-]+", "", normalize(text))


def group_models(rows):
    groups = defaultdict(lambda: defaultdict(list))
    owners = {}
    for row in rows:
        model = model_key(row["model"])
        family = row["category"]
        if not model or owners.setdefault(model, family) != family:
            raise ValueError(f"Empty or cross-family model key: {model!r}")
        groups[family][model].append(row)
    return groups


def split_models(groups, seed):
    train, test = [], []
    for family, models in sorted(groups.items()):
        keys = sorted(models)
        if len(keys) < 2:
            raise ValueError(f"Cannot hold out a model for {family}")
        random.Random(f"split|{seed}|{family}").shuffle(keys)
        n_test = max(1, round(len(keys) * 0.2))
        test.extend(row for key in keys[:n_test] for row in models[key])
        train.extend(row for key in keys[n_test:] for row in models[key])
    if {model_key(r["model"]) for r in train} & {model_key(r["model"]) for r in test}:
        raise ValueError("Model leakage across the split")
    if {normalize(r["prompt"]) for r in train} & {normalize(r["prompt"]) for r in test}:
        raise ValueError("Duplicate input across the split")
    return train, test


def metrics(rows, predictions):
    gold = [r["category"] for r in rows]
    support = Counter(gold)
    predicted = Counter(predictions)
    correct = Counter(g for g, p in zip(gold, predictions, strict=True) if g == p)
    return {
        "accuracy": sum(correct.values()) / len(rows),
        "macro_f1": mean(2 * correct[f] / (support[f] + predicted[f]) for f in support),
        "per_family_recall": {f: correct[f] / n for f, n in sorted(support.items())},
    }


def nearest(test_x, train_x, owners):
    return [owners[int(i)] for i in np.argmax(test_x @ train_x.T, axis=1)]


def predict(memory, rows):
    return [memory.suggest(row["prompt"], k=0) for row in rows]


def holdout_trial(groups, negatives, seed):
    train, test = split_models(groups, seed)
    vocab = build_vocab([row["prompt"] for row in train])
    train_x = embed_many([row["prompt"] for row in train], vocab)
    test_x = embed_many([row["prompt"] for row in test], vocab)
    owners = [row["category"] for row in train]
    memory = MetaMemory()
    memory.vocab = vocab
    memory.observe_batch(train)
    suggestions = predict(memory, test)
    pred_all = nearest(test_x, train_x, owners)
    retained_x, retained_owners, _ = memory._pool_matrix()
    pred_retained = nearest(test_x, retained_x, retained_owners)

    result = {"seed": seed, "train_prompts": len(train), "stored_examples": len(retained_owners)}
    for name, indices in (
        ("all", list(range(len(test)))),
        ("bare", [i for i, row in enumerate(test) if row["variant"] == "裸型号"]),
    ):
        rows = [test[i] for i in indices]
        accepted = [i for i in indices if suggestions[i]["confidence"] >= CONFIDENCE_THRESHOLD]
        correct = sum(suggestions[i]["family"] == test[i]["category"] for i in accepted)
        result[name] = {
            "n": len(rows),
            "hybrid": metrics(rows, [suggestions[i]["family"] for i in indices]),
            "nearest_all": metrics(rows, [pred_all[i] for i in indices]),
            "nearest_retained": metrics(rows, [pred_retained[i] for i in indices]),
            "hint_coverage": len(accepted) / len(rows),
            "hint_accuracy_when_accepted": correct / len(accepted) if accepted else None,
        }
    result["negative_probes"] = [
        {"id": row["id"], "prompt": row["prompt"], "family": s["family"],
         "confidence": s["confidence"], "hint": s["confidence"] >= CONFIDENCE_THRESHOLD}
        for row, s in zip(negatives, predict(memory, negatives), strict=True)
    ]
    return result


def bare_and_labeled(model_rows):
    bare = next((r for r in model_rows if r["variant"] == "裸型号"), None)
    labeled = next((r for r in model_rows if r["variant"] == "带类别词"), None)
    return bare, labeled


def transfer_trial(groups, seed):
    # Match the families, support model identities, and target queries across styles.
    pairs_by_family = {
        family: [pair for _, rows in sorted(models.items())
                 if all(pair := bare_and_labeled(rows))]
        for family, models in sorted(groups.items())
    }
    pairs_by_family = {family: pairs for family, pairs in pairs_by_family.items() if len(pairs) >= 4}
    for family, pairs in pairs_by_family.items():
        random.Random(f"transfer|{seed}|{family}").shuffle(pairs)
    query = [pair[0] for pairs in pairs_by_family.values() for pair in pairs[3:]]
    result = {"seed": seed, "families": list(pairs_by_family), "n_query": len(query)}
    for shots in (1, 2, 3):
        result[str(shots)] = {}
        for name, index in (("bare_support", 0), ("labeled_support", 1)):
            memory = MetaMemory()  # Fixed built-in features in both branches.
            support = [pair[index] for pairs in pairs_by_family.values() for pair in pairs[:shots]]
            memory.observe_batch(support)
            pred = [s["family"] for s in predict(memory, query)]
            result[str(shots)][name] = metrics(query, pred)
    return result


def bare_rows(groups):
    return {
        family: [row for _, rows in sorted(models.items())
                 if (row := bare_and_labeled(rows)[0]) is not None]
        for family, models in sorted(groups.items())
    }


def unit(vector):
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def episodic_features(row):
    """Label-free encoding that can represent a previously unseen model prefix."""
    text = normalize("^" + row["prompt"] + "$")
    lexical = np.zeros(1024, dtype=np.float32)
    for size in (2, 3, 4):
        for i in range(len(text) - size + 1):
            slot = crc32(text[i:i + size].encode("utf-8")) % len(lexical)
            lexical[slot] += 1
    numeric = np.zeros(6, dtype=np.float32)
    values = numeric_tokens(row["prompt"])
    numeric[:len(values)] = values
    return unit(np.log1p(lexical)), unit(numeric)


def episodic_trial(groups, seed):
    """Fit one shared distance weight on other families, then adapt with one labeled model."""
    rows = {f: r for f, r in bare_rows(groups).items() if len(r) >= 2}
    families = sorted(rows)
    random.Random(f"meta-fold|{seed}").shuffle(families)
    features = {r["id"]: episodic_features(r) for group in rows.values() for r in group}
    weights = (0.0, 0.25, 0.55, 1.0, 2.0)
    encoded = {
        w: {key: unit(np.concatenate((lex, w * num))) for key, (lex, num) in features.items()}
        for w in weights
    }

    def episodes(pool, count, label):
        rng = random.Random(f"meta|{seed}|{label}")
        trials = []
        for _ in range(count):
            support, query = [], []
            for f in sorted(rng.sample(pool, 6)):
                samples = rng.sample(rows[f], min(5, len(rows[f])))
                support.append(samples[0])
                query.extend(samples[1:])
            trials.append((support, query))
        return trials

    def score(trials, weight):
        embs = encoded[weight]
        values = []
        for support, query in trials:
            sx = np.stack([embs[r["id"]] for r in support])
            qx = np.stack([embs[r["id"]] for r in query])
            prediction = nearest(qx, sx, [r["category"] for r in support])
            recalls = metrics(query, prediction)["per_family_recall"]
            values.append(mean(recalls.values()))
        return mean(values)

    folds = []
    for i in range(3):
        test_families = families[i::3]
        train_families = sorted(set(families) - set(test_families))
        train = episodes(train_families, 40, f"train-{i}")
        test = episodes(test_families, 20, f"test-{i}")
        train_scores = {w: score(train, w) for w in weights}
        selected = max(weights, key=lambda w: (train_scores[w], -abs(w - 0.55)))
        folds.append({
            "train_families": train_families, "test_families": test_families,
            "train_macro_recall": train_scores, "weight": selected,
            "test_macro_recall": {"learned": score(test, selected), "fixed": score(test, 0.55),
                                  "char_only": score(test, 0.0)},
        })
    return {"seed": seed, "folds": folds}


def class_stream_trial(groups, seed, ordering):
    bare_models = {family: rows for family, rows in bare_rows(groups).items() if len(rows) >= 2}
    families = sorted(bare_models, key=lambda f: (-len(bare_models[f]), f))
    if ordering == "reverse":
        families.reverse()
    elif ordering == "shuffled":
        random.Random(f"order|{seed}").shuffle(families)
    train, test = split_models(
        {f: {model_key(r["model"]): [r] for r in seq} for f, seq in bare_models.items()}, seed
    )
    demos = defaultdict(list)
    for row in train:
        demos[row["category"]].append(row)
    memory = MetaMemory()
    vocab = memory.vocab
    test_x = embed_many([row["prompt"] for row in test], vocab)
    histories = {"hybrid": [], "nearest_retained": []}
    phase_rows = []

    def snapshot(family, phase, count):
        retained_x, owners, _ = memory._pool_matrix()
        predictions = {
            "hybrid": [s["family"] for s in predict(memory, test)],
            "nearest_retained": nearest(test_x, retained_x, owners),
        }
        active = set(owners)
        indices = [i for i, row in enumerate(test) if row["category"] in active]
        for method, pred in predictions.items():
            histories[method].append(metrics([test[i] for i in indices], [pred[i] for i in indices]))
        phase_rows.append({"family": family, "phase": phase, "n_labeled_models": count})

    for family in families:
        samples = demos[family][:3]
        memory.observe_batch(samples)
        snapshot(family, "introduce", len(samples))
    for family in families:
        samples = demos[family][3:]
        if samples:
            memory.observe_batch(samples)
            snapshot(family, "update", len(samples))

    result = {"seed": seed, "ordering": ordering, "n_query": len(test), "steps": phase_rows}
    for name, history in histories.items():
        recalls = [entry["per_family_recall"] for entry in history]
        after_intro = recalls[len(families) - 1]
        final = recalls[-1]
        eligible = families[:-1]  # Each had later introductions.
        introduction_drops = {
            f: max(frame[f] for frame in recalls[i:len(families)]) - after_intro[f]
            for i, f in enumerate(eligible)
        }
        update_drops = {f: after_intro[f] - final[f] for f in families}
        result[name] = {
            "introduction_peak_to_end_drop": mean(introduction_drops.values()),
            "introduction_max_family_drop": max(introduction_drops.values()),
            "post_update_mean_drop": mean(update_drops.values()),
            "post_update_max_family_drop": max(update_drops.values()),
            "final": history[-1],
            "trajectory": history,
        }
    return result


def matched_catalog(rows, negatives):
    index = {model_key(row["model"]): row["category"] for row in rows}
    bare = [row for row in rows if row["variant"] == "裸型号"]
    return {
        "intent": "Known catalog lookup across the complete index, not new-model generalization",
        "n_bare": len(bare),
        "bare_correct": sum(index.get(model_key(row["prompt"])) == row["category"] for row in bare),
        "negative_rejected": sum(model_key(row["prompt"]) not in index for row in negatives),
    }


def baseline(rows):
    record = {
        "holdout": evaluate_holdout(rows),
        "variants": evaluate_variant_ablation(rows),
        "incremental": evaluate_incremental(rows),
        "incremental_hard": {n: evaluate_incremental_hard(rows, stream_per_family=n)
                             for n in (10, 50, 200)},
    }
    for group in (None, "model"):
        train, test = _split_by_family(rows, 0.8, 0, group)
        train_keys = {model_key(r["model"]) for r in train}
        record[f"overlap_{group or 'row'}"] = {
            "n_test": len(test),
            "seen_model": sum(model_key(r["model"]) in train_keys for r in test),
        }
    return record


def revision():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=10)
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")

    data = load_dataset(exclude_null_category=False)
    rows = [row for row in data if row["category"]]
    negatives = [row for row in data if not row["category"]]
    groups = group_models(rows)
    result = {
        "revision": revision(),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "dataset_sha256": sha256(DATASET_PATH.read_bytes()).hexdigest(),
        "n_positive": len(rows),
        "n_negative": len(negatives),
        "n_models": sum(map(len, groups.values())),
        "family_counts": {f: sum(map(len, g.values())) for f, g in sorted(groups.items())},
        "catalog_lookup": matched_catalog(rows, negatives),
        "historical_baseline": baseline(rows),
        "group_holdout": [],
        "transfer": [],
        "episodic": [],
        "class_stream": [],
    }
    for seed in range(args.seeds):
        result["group_holdout"].append(holdout_trial(groups, negatives, seed))
        result["transfer"].append(transfer_trial(groups, seed))
        result["episodic"].append(episodic_trial(groups, seed))
        for ordering in ("frequent_first", "reverse", "shuffled"):
            result["class_stream"].append(class_stream_trial(groups, seed, ordering))
        print(f"Evaluated seed {seed}", flush=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
                           encoding="utf-8")


if __name__ == "__main__":
    main()
