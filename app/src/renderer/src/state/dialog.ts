import { create } from 'zustand'

export type Field =
  | { key: string; label: string; type: 'text'; value: string }
  | { key: string; label: string; type: 'number'; value: number; min?: number; max?: number; step?: number; suffix?: string }
  | { key: string; label: string; type: 'select'; value: string; options: { value: string; label: string }[] }
  | { key: string; label: string; type: 'checkbox'; value: boolean }
  | { key: string; label: string; type: 'note'; value: string }

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

/** A question with its own buttons (e.g. Save / Don't Save / Cancel); Escape picks ``cancel``. */
export type ChoiceSpec = {
  title: string
  message: string
  buttons: { label: string; value: string; primary?: boolean }[]
  cancel: string
}

type PendingChoice = ChoiceSpec & { resolve: (value: string) => void }
export const useChoice = create<{ choice: PendingChoice | null }>(() => ({ choice: null }))

export function askChoice(spec: ChoiceSpec): Promise<string> {
  return new Promise((resolve) => {
    useChoice.getState().choice?.resolve(useChoice.getState().choice!.cancel)
    useChoice.setState({ choice: { ...spec, resolve } })
  })
}

export function answerChoice(value: string): void {
  const c = useChoice.getState().choice
  useChoice.setState({ choice: null })
  c?.resolve(value)
}
