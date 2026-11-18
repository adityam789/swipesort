import type { FolderOut } from '../api/types'

/** Subsequence match score; null when `query` is not a subsequence of `text`. Higher is better. */
export function fuzzyScore(query: string, text: string): number | null {
  const q = query.toLowerCase().replace(/\s+/g, '')
  const t = text.toLowerCase()
  if (!q) return 0
  let score = 0
  let ti = 0
  let prev = -2
  for (const ch of q) {
    const idx = t.indexOf(ch, ti)
    if (idx < 0) return null
    score += 1
    if (idx === prev + 1) score += 3 // consecutive
    if (idx === 0 || '/ _-.'.includes(t[idx - 1])) score += 2 // word / segment start
    prev = idx
    ti = idx + 1
  }
  // Prefer shorter paths, and matches within the folder's own name.
  score -= t.length * 0.05
  return score
}

export function filterFolders(folders: FolderOut[], query: string): FolderOut[] {
  if (!query.trim()) return folders
  const scored: { f: FolderOut; s: number }[] = []
  for (const f of folders) {
    const full = fuzzyScore(query, f.path)
    const base = fuzzyScore(query, f.name)
    if (full === null && base === null) continue
    scored.push({ f, s: Math.max(full ?? -Infinity, base === null ? -Infinity : base + 5) })
  }
  return scored.sort((a, b) => b.s - a.s).map((x) => x.f)
}
