import { create } from 'zustand'

export type Field =
  | { key: string; label: string; type: 'text'; value: string }
  | { key: string; label: string; type: 'number'; value: number; min?: number; max?: number; step?: number; suffix?: string }
  | { key: string; label: string; type: 'select'; value: string; options: { value: string; label: string }[] }
  | { key: string; label: string; type: 'checkbox'; value: boolean }

export type DialogSpec = {
  title: string
  fields: Field[]
  okLabel?: string
  onSubmit: (values: Record<string, string | number | boolean>) => void | Promise<void>
}

export const useDialog = create<{ dialog: DialogSpec | null }>(() => ({ dialog: null }))

export function openDialog(spec: DialogSpec): void {
  useDialog.setState({ dialog: spec })
}

export function closeDialog(): void {
  useDialog.setState({ dialog: null })
}
