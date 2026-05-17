import type { DragEndEvent } from "@dnd-kit/core"
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core"
import { restrictToVerticalAxis } from "@dnd-kit/modifiers"
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import React, { useEffect, useMemo, useState } from "react"
import {
  ComponentProps,
  Streamlit,
  withStreamlitConnection,
} from "streamlit-component-lib"

type Item = { id: string; title: string; subtitle?: string }

type Args = {
  items?: Item[]
  revision?: number
  disabled?: boolean
  frameHeight?: number
}

function DragRow({ item, isDark }: { item: Item; isDark: boolean }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.9 : 1,
    zIndex: isDragging ? 2 : undefined,
  } satisfies React.CSSProperties

  const panelBg = isDark ? "rgba(110,168,254,0.08)" : "rgba(151,166,195,0.12)"
  const borderCol = isDark ? "rgba(110,168,254,0.38)" : "rgba(148,163,184,0.45)"
  const titleCol = isDark ? "#f0f6fc" : "#111827"
  const subCol = isDark ? "#8d96a9" : "#4b5563"
  const handleBg = isDark ? "rgba(177,186,196,0.14)" : "rgba(249,250,251,0.95)"

  return (
    <div
      ref={setNodeRef}
      style={{
        ...style,
        display: "flex",
        gap: 10,
        alignItems: "flex-start",
        padding: "8px 10px",
        marginBottom: 6,
        borderRadius: 8,
        background: panelBg,
        border: `1px solid ${borderCol}`,
        boxShadow: isDragging ? "0 14px 30px rgba(0,0,0,0.35)" : "none",
      }}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label="Drag to reorder steps"
        style={{
          cursor: "grab",
          flex: "0 0 auto",
          padding: "5px 8px",
          borderRadius: 6,
          border: `1px solid ${borderCol}`,
          background: handleBg,
          color: subCol,
          lineHeight: 1,
          fontWeight: 800,
          fontSize: 13,
          fontFamily: "inherit",
        }}
      >
        ≡
      </button>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontWeight: 650,
            color: titleCol,
            wordBreak: "break-word",
            fontSize: "0.95rem",
          }}
        >
          {item.title}
        </div>
        {item.subtitle ? (
          <div
            style={{
              marginTop: 4,
              fontSize: "0.82rem",
              color: subCol,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {item.subtitle}
          </div>
        ) : null}
      </div>
    </div>
  )
}

const Inner = (props: ComponentProps) => {
  const args = props.args as Args
  const itemsRaw = Array.isArray(args.items) ? args.items : []
  const items = itemsRaw.filter((it): it is Item => Boolean(it) && typeof it.id === "string")
  const revision = typeof args.revision === "number" ? args.revision : 0
  const disabledWidget = Boolean(args.disabled || props.disabled)
  const frameHeight =
    typeof args.frameHeight === "number" ? args.frameHeight : 320

  const theme = props.theme as { base?: string } | undefined
  const isDark = theme?.base === "dark"

  const [orderedIds, setOrderedIds] = useState<string[]>(() =>
    items.map((i) => i.id),
  )

  const fingerprint = JSON.stringify(items.map((it) => [it.id, it.title]))

  useEffect(() => {
    setOrderedIds(items.map((i) => i.id))
    // fingerprint keeps React from fighting Streamlit iframe reloads mid-render
    // eslint-disable-next-line react-hooks/exhaustive-deps -- revision + content hash
  }, [revision, fingerprint])

  const itemMap = useMemo(() => {
    const m = new Map<string, Item>()
    for (const it of items) m.set(it.id, it)
    return m
  }, [items])

  useEffect(() => {
    Streamlit.setFrameHeight(frameHeight + 8)
  }, [frameHeight, items.length, revision])

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  )

  const onDragEnd = (evt: DragEndEvent) => {
    if (disabledWidget || items.length < 2) return
    const overIdRaw = evt.over?.id ? String(evt.over.id) : null
    const activeId = evt.active?.id ? String(evt.active.id) : null
    if (!activeId || !overIdRaw || activeId === overIdRaw) return

    setOrderedIds((prev) => {
      const idxA = prev.indexOf(activeId)
      const idxB = prev.indexOf(overIdRaw)
      if (idxA < 0 || idxB < 0) return prev
      const next = arrayMove(prev, idxA, idxB)
      if (next.every((id, ix) => id === prev[ix])) return prev
      Streamlit.setComponentValue({ order: next, revision })
      return next
    })
  }

  const rows = orderedIds
    .map((id) => itemMap.get(id))
    .filter((x): x is Item => Boolean(x))

  return (
    <div
      style={{
        opacity: disabledWidget ? 0.62 : 1,
        pointerEvents: disabledWidget ? "none" : "auto",
        background: "transparent",
        paddingBottom: 4,
      }}
    >
      {rows.length ? (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
          modifiers={[restrictToVerticalAxis]}
        >
          <SortableContext
            items={orderedIds.filter((id) => itemMap.has(id))}
            strategy={verticalListSortingStrategy}
          >
            <div role="list" aria-label="Reorderable scenario steps">
              {rows.map((it) => (
                <DragRow key={`${revision}-${it.id}`} item={it} isDark={isDark} />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      ) : (
        <div
          style={{
            padding: "8px 4px",
            color: isDark ? "#94a3b8" : "#6b7280",
            fontSize: "0.9rem",
          }}
        >
          No steps yet.
        </div>
      )}
    </div>
  )
}

export default withStreamlitConnection(Inner)
