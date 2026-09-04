import { AnimatePresence, motion } from 'motion/react'
import { CapsuleToast } from './ios'
import { dismissToast, useToasts } from '../lib/toast'

// One fixed region for every transient message in the app, mounted once from
// AppShell.
//
// The alternative -- and what the app did before -- is rendering feedback in
// document flow at the call site. Two consequences, both of which DESIGN.md now
// names as layout bugs: the player's error note was injected between the effects
// panel and the queue heading, so a failure shoved the page around; and the
// music undo was placed after the queue list, so with a long queue it spent its
// entire five-second life below the fold. "Undo instead of confirm" was
// implemented and unreachable.
//
// Positioned like .mini-player (top-right on desktop, above the tab bar on
// mobile) and deliberately *not* a flex child of #root, which is a column flex
// container -- an in-flow region would change the page height as toasts arrive,
// which is the defect being fixed.
export function ToastHost() {
  const toasts = useToasts()
  return (
    // aria-live on the region announces arrivals politely; each error toast
    // additionally carries role="alert" from CapsuleToast, which interrupts.
    // The region stays mounted and empty rather than conditionally rendered:
    // a live region added to the DOM at the same moment as its content is not
    // reliably announced.
    <div className="toast-region" aria-live="polite" aria-relevant="additions">
      <AnimatePresence initial={false}>
        {toasts.map(toast => (
          <motion.div
            key={toast.id}
            layout
            initial={{ opacity: 0, y: -8, scale: .96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, scale: .96, transition: { duration: .15 } }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          >
            <CapsuleToast tone={toast.tone} action={toast.action && { label: toast.action.label, onClick: () => { toast.action!.onClick(); dismissToast(toast.id) } }} onDismiss={() => dismissToast(toast.id)}>{toast.message}</CapsuleToast>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
