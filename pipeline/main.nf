#!/usr/bin/env nextflow
/*
 * Germline variant calling for a validated batch:
 *   fastp (QC/trim) -> bwa-mem2 align -> samtools sort/index -> bcftools call
 *
 * Inputs come straight from Object Storage through its S3 Compatibility API,
 * and results are published back to results/<batch_id>/.
 */
nextflow.enable.dsl = 2

params.batch_id     = null
params.samplesheet  = null       // the batch's sample sheet CSV
params.input_prefix = null       // e.g. s3://genome-intake-data/raw/<batch_id>
params.reference    = null       // FASTA
params.outdir       = null       // e.g. s3://genome-intake-data/results/<batch_id>

process BWA_INDEX {
    tag "${fasta.simpleName}"
    input:
    path fasta

    output:
    tuple path(fasta), path("${fasta}.*"), emit: index

    script:
    """
    bwa-mem2 index ${fasta}
    samtools faidx ${fasta}
    """
}

process FASTP {
    tag "${sample_id}"
    publishDir "${params.outdir}/qc", mode: 'copy', pattern: '*.json'
    cpus 2

    input:
    tuple val(sample_id), path(r1), path(r2)

    output:
    tuple val(sample_id), path("${sample_id}_R1.trim.fastq.gz"), path("${sample_id}_R2.trim.fastq.gz"), emit: reads
    path "${sample_id}.fastp.json", emit: json

    script:
    """
    fastp -i ${r1} -I ${r2} \\
          -o ${sample_id}_R1.trim.fastq.gz -O ${sample_id}_R2.trim.fastq.gz \\
          --json ${sample_id}.fastp.json --html /dev/null --thread ${task.cpus}
    """
}

process ALIGN {
    tag "${sample_id}"
    cpus 2

    input:
    tuple val(sample_id), path(r1), path(r2)
    tuple path(fasta), path(index)

    output:
    tuple val(sample_id), path("${sample_id}.bam"), path("${sample_id}.bam.bai")

    script:
    """
    bwa-mem2 mem -t ${task.cpus} -R '@RG\\tID:${sample_id}\\tSM:${sample_id}\\tPL:ILLUMINA' ${fasta} ${r1} ${r2} \\
      | samtools sort -@ ${task.cpus} -o ${sample_id}.bam -
    samtools index ${sample_id}.bam
    """
}

process CALL_VARIANTS {
    tag "${sample_id}"
    publishDir "${params.outdir}/vcf", mode: 'copy'

    input:
    tuple val(sample_id), path(bam), path(bai)
    tuple path(fasta), path(index)

    output:
    tuple val(sample_id), path("${sample_id}.vcf.gz"), path("${sample_id}.vcf.gz.csi")

    script:
    """
    bcftools mpileup -f ${fasta} ${bam} -Ou \\
      | bcftools call -mv -Oz -o ${sample_id}.vcf.gz
    bcftools index ${sample_id}.vcf.gz
    """
}

workflow {
    if (!params.samplesheet || !params.input_prefix || !params.reference || !params.outdir) {
        error "Set --samplesheet, --input_prefix, --reference and --outdir"
    }

    reads = Channel
        .fromPath(params.samplesheet)
        .splitCsv(header: true)
        .map { row -> tuple(row.sample_id, file("${params.input_prefix}/${row.fastq_1}"), file("${params.input_prefix}/${row.fastq_2}")) }

    index = BWA_INDEX(file(params.reference)).index.first()
    trimmed = FASTP(reads)
    bams = ALIGN(trimmed.reads, index)
    CALL_VARIANTS(bams, index)
}

workflow.onComplete {
    log.info "Batch ${params.batch_id}: ${workflow.success ? 'COMPLETE' : 'FAILED'} in ${workflow.duration}"
}
