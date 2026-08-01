import { PressableButton, Sheet } from './ios'

export function ConfirmSheet({ open, onOpenChange, title, description, confirmLabel = 'Confirm', pending = false, onConfirm }: {
  open: boolean; onOpenChange(value: boolean): void; title: string; description: string; confirmLabel?: string; pending?: boolean; onConfirm(): void
}) {
  return <Sheet open={open} onOpenChange={onOpenChange} label={title}>
    <h2>{title}</h2><p>{description}</p>
    <div className="sheet-actions">
      <PressableButton variant="secondary" onClick={() => onOpenChange(false)}>Cancel</PressableButton>
      <PressableButton variant="danger" disabled={pending} onClick={onConfirm}>{pending ? 'Working…' : confirmLabel}</PressableButton>
    </div>
  </Sheet>
}
