from os.path import dirname, join

import setuptools


def readme() -> str:
    return open(join(dirname(__file__), "README.md"), encoding="utf-8").read()


setuptools.setup(
    name="streamlit-dnd-sortable",
    version="0.1.0",
    description="Streamlit sortable list with @dnd-kit drag-and-drop.",
    long_description=readme(),
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(),
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "streamlit>=1.39",
    ],
)
