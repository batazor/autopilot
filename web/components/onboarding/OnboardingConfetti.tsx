"use client";

import { useEffect, useState } from "react";
import Confetti from "react-confetti";

const CONFETTI_COLORS = [
  "#38bdf8",
  "#22c55e",
  "#f59e0b",
  "#f43f5e",
  "#a78bfa",
  "#f8fafc",
];

function viewportSize() {
  if (typeof window === "undefined") return { width: 0, height: 0 };
  return { width: window.innerWidth, height: window.innerHeight };
}

function reducedMotionRequested() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function OnboardingConfetti({ active }: { active: boolean }) {
  const [size, setSize] = useState(viewportSize);
  const [reducedMotion, setReducedMotion] = useState(reducedMotionRequested);
  const [visible, setVisible] = useState(active && !reducedMotionRequested());

  useEffect(() => {
    const onResize = () => setSize(viewportSize());
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    if (active && !reducedMotion) {
      setVisible(true);
    } else if (!active || reducedMotion) {
      setVisible(false);
    }
  }, [active, reducedMotion]);

  if (!visible || size.width <= 0 || size.height <= 0) return null;

  return (
    <Confetti
      aria-hidden="true"
      className="onboarding-confetti"
      width={size.width}
      height={size.height}
      recycle={false}
      numberOfPieces={260}
      tweenDuration={4200}
      gravity={0.14}
      initialVelocityY={18}
      colors={CONFETTI_COLORS}
      onConfettiComplete={() => setVisible(false)}
    />
  );
}
