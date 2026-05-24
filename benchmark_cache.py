import time

from domain_scout.matching.entity_match import normalize_org_name, org_name_similarity


def run_benchmark():
    names = [
        "Apple Inc.",
        "Microsoft Corporation",
        "Google LLC",
        "Amazon.com, Inc.",
        "Meta Platforms, Inc.",
        "Acme Solutions, Inc.",
        "Siemens AG",
        "Volvo AB",
        "UnitedHealth Group",
        "Goldman Sachs Group Inc",
    ]

    start = time.time()
    for _ in range(10000):
        for name in names:
            normalize_org_name(name)
    end = time.time()
    print(f"normalize_org_name: {end - start:.4f}s")

    start = time.time()
    for _ in range(1000):
        for name1 in names:
            for name2 in names:
                org_name_similarity(name1, name2)
    end = time.time()
    print(f"org_name_similarity: {end - start:.4f}s")


if __name__ == "__main__":
    run_benchmark()
