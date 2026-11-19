import { SHEET } from './constants'

/** Pure: does releasing the sheet at this offset/velocity (px, px/s; down = positive) dismiss it? */
export function shouldDismissSheet(offsetY: number, velocityY: number): boolean {
  return offsetY >= SHEET.dismissDistance || (offsetY > 0 && velocityY >= SHEET.dismissVelocity)
}
