export default function NotFound() {
  return (
    <main className="flex h-screen items-center justify-center px-4 py-4">
      <section className="panel scanline max-w-[720px] px-8 py-10 text-center">
        <p className="pixel-label"># 404</p>
        <h1 className="pixel-title mt-4 text-[1rem] text-[var(--color-ink)] md:text-[1.15rem]">
          Route not found
        </h1>
        <p className="mono mt-5 text-[1.2rem] text-[var(--color-ink-soft)]">
          The requested screen does not exist in this workspace shell.
        </p>
      </section>
    </main>
  );
}
