from os.path import dirname, join

import setuptools


def readme() -> str:
    return open(join(dirname(__file__), "README.md")).read()


setuptools.setup(
    name="streamlit-nested-table",
    version="0.1.0",
    description="Streamlit nested / expandable table built on TanStack Table + Tailwind CSS.",
    long_description=readme(),
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(),
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "streamlit>=1.39",
    ],
)
