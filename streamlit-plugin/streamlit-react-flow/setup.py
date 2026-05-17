from os.path import dirname, join

import setuptools


def readme() -> str:
    return open(join(dirname(__file__), "README.md")).read()


setuptools.setup(
    name="streamlit-react-flow",
    version="0.1.0",
    description="Streamlit component for node graphs using @xyflow/react (React Flow v12).",
    long_description=readme(),
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(),
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "streamlit>=1.39",
    ],
)
