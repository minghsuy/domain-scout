import typer
from typing import Annotated

app = typer.Typer()

@app.command()
def main(
    **kwargs
):
    print("hi", kwargs)

if __name__ == "__main__":
    app()
