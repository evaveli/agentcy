import apiClient from './client'
import type { PipelineConfig, PipelineRun } from './types'

export async function listPipelines(username: string): Promise<PipelineConfig[]> {
  const { data } = await apiClient.get(`/pipelines/${username}`)
  return Array.isArray(data) ? data : data.pipelines ?? []
}

export async function getPipeline(username: string, pipelineId: string): Promise<PipelineConfig> {
  const { data } = await apiClient.get(`/pipelines/${username}/${pipelineId}`)
  return data
}

export async function createPipeline(username: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.post(`/pipelines/${username}`, payload)
  return data
}

export async function updatePipeline(username: string, pipelineId: string, payload: Record<string, unknown>): Promise<unknown> {
  const { data } = await apiClient.put(`/pipelines/${username}/${pipelineId}`, payload)
  return data
}

export async function deletePipeline(username: string, pipelineId: string): Promise<void> {
  await apiClient.delete(`/pipelines/${username}/${pipelineId}`)
}

export async function listRuns(username: string, pipelineId: string): Promise<string[]> {
  const { data } = await apiClient.get(`/pipelines/${username}/${pipelineId}/runs`)
  return Array.isArray(data) ? data : data.runs ?? []
}

export async function getRun(username: string, pipelineId: string, runId: string): Promise<PipelineRun> {
  const { data } = await apiClient.get(`/pipelines/${username}/${pipelineId}/${runId}`)
  return data
}

export async function startPipelineRun(username: string, pipelineId: string): Promise<unknown> {
  const { data } = await apiClient.post(`/pipelines/${username}/${pipelineId}/start`)
  return data
}

export async function getTaskOutput(
  username: string,
  pipelineId: string,
  runId: string,
  taskId: string,
): Promise<Record<string, unknown>> {
  const { data } = await apiClient.get(
    `/pipelines/${username}/${pipelineId}/${runId}/tasks/${taskId}/output`,
  )
  return data
}

export async function getPipelineSchema(): Promise<Record<string, unknown>> {
  const { data } = await apiClient.get('/schema/pipeline')
  return data
}
