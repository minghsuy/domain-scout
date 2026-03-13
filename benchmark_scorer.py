import time
from domain_scout.scorer import _entity_name_in_org

company_name = "Walmart Inc"
cert_org_names = set([
    "Some Random Org",
    "Another Random Org",
    "Yet Another Random Org",
] + [f"Random Org {i}" for i in range(200)])

def run_benchmark():
    start = time.perf_counter()
    for _ in range(100000):
        _entity_name_in_org(company_name, cert_org_names)
    end = time.perf_counter()
    print(f"Time taken: {end - start:.4f} seconds")

if __name__ == "__main__":
    run_benchmark()
