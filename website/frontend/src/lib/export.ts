import type { AIHistoryDetail } from '../types/api'

/** A readable Markdown copy of a DM conversation: the summary first, then the retained messages in order. */
export function buildTranscriptMarkdown(detail: Pick<AIHistoryDetail, 'channel_id' | 'rolling_summary' | 'messages'>, exportedAt = new Date()) {
  const lines = [`# DM conversation ${detail.channel_id}`, '', `Exported ${exportedAt.toISOString()}`, '', '## Summary of compacted messages', '', detail.rolling_summary?.trim() || '_No summary._', '', '## Messages', '']
  if (!detail.messages.length) lines.push('_No retained messages._', '')
  for (const message of detail.messages) {
    lines.push(`**${message.role}**${message.created_at ? ` · ${message.created_at}` : ''}`, '', message.content, '')
  }
  return lines.join('\n')
}

/** Offer `text` to the browser as a file download. */
export function downloadText(filename: string, text: string, mime = 'text/markdown') {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
