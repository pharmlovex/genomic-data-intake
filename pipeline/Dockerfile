# One image with every tool the pipeline needs, pinned via conda-forge/bioconda.
#   docker build --platform linux/amd64 -t <ocir_prefix>/pipeline-tools:0.1.0 pipeline/
FROM mambaorg/micromamba:1.5.10

RUN micromamba install -y -n base -c conda-forge -c bioconda \
        bwa-mem2=2.2.1 samtools=1.21 bcftools=1.21 fastp=0.23.4 procps-ng \
 && micromamba clean -a -y

ENV PATH=/opt/conda/bin:$PATH
