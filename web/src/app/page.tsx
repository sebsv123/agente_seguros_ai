import { ShieldCheck, HeartPulse, Home, MessageCircle, Sparkles, Clock, CheckCircle2 } from "lucide-react";

const WHATSAPP_LINK = "https://wa.me/34TU_NUMERO_AQUI?text=" + encodeURIComponent(
  "Hola 👋 Vengo desde la web. Quiero una propuesta de seguro y asesoramiento."
);

export default function Page() {
  return (
    <main className="min-h-screen bg-white text-zinc-900">
      <header className="sticky top-0 z-50 bg-white/80 backdrop-blur border-b border-zinc-100">
        <div className="mx-auto max-w-6xl px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" />
            <span className="font-semibold tracking-tight">Valentín Protección Integral</span>
          </div>
          <a
            href={WHATSAPP_LINK}
            className="inline-flex items-center gap-2 rounded-full bg-zinc-900 text-white px-4 py-2 text-sm hover:bg-zinc-800 transition"
          >
            <MessageCircle className="h-4 w-4" />
            Hablar por WhatsApp
          </a>
        </div>
      </header>

      {/* HERO */}
      <section className="mx-auto max-w-6xl px-5 pt-14 pb-10">
        <div className="grid gap-8 md:grid-cols-2 md:items-center">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full bg-zinc-50 border border-zinc-200 px-3 py-1 text-xs text-zinc-700">
              <Sparkles className="h-3.5 w-3.5" />
              Asesoramiento claro. Sin vueltas.
            </p>
            <h1 className="mt-4 text-4xl md:text-5xl font-semibold tracking-tight">
              Seguros que encajan contigo, explicados como persona.
            </h1>
            <p className="mt-4 text-zinc-600 text-lg leading-relaxed">
              Te ayudo a elegir cobertura de <b>Salud</b>, <b>Vida</b> u <b>Hogar</b> con preguntas simples,
              comparativa real y una propuesta lista para contratar por WhatsApp.
            </p>

            <div className="mt-6 flex flex-wrap items-center gap-3">
              <a
                href={WHATSAPP_LINK}
                className="inline-flex items-center gap-2 rounded-full bg-zinc-900 text-white px-5 py-2.5 text-sm hover:bg-zinc-800 transition"
              >
                <MessageCircle className="h-4 w-4" />
                Quiero una propuesta
              </a>
              <a
                href="#form"
                className="inline-flex items-center gap-2 rounded-full border border-zinc-300 px-5 py-2.5 text-sm hover:bg-zinc-50 transition"
              >
                Enviar mis datos (1 min)
              </a>
            </div>

            <div className="mt-8 grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm text-zinc-600">
              <Badge icon={<Clock className="h-4 w-4" />} text="Respuesta rápida" />
              <Badge icon={<CheckCircle2 className="h-4 w-4" />} text="Preguntas simples" />
              <Badge icon={<ShieldCheck className="h-4 w-4" />} text="Proceso ordenado" />
            </div>
          </div>

          <div className="rounded-3xl border border-zinc-200 bg-gradient-to-b from-zinc-50 to-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold tracking-tight">Cómo trabajamos</h2>
            <ol className="mt-4 space-y-4 text-sm text-zinc-700">
              <Step n="1" title="Te hago 6–8 preguntas clave" desc="Provincia, edades, copagos, preexistencias y preferencias." />
              <Step n="2" title="Te preparo propuesta y opciones" desc="Te explico diferencias importantes sin ruido." />
              <Step n="3" title="Te lo dejo listo para WhatsApp" desc="Mensaje prellenado y seguimos por ahí para cerrar." />
            </ol>

            <div className="mt-6 rounded-2xl bg-white border border-zinc-200 p-4">
              <p className="text-xs text-zinc-500">Atajos</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Pill icon={<HeartPulse className="h-4 w-4" />} text="Salud" />
                <Pill icon={<ShieldCheck className="h-4 w-4" />} text="Vida" />
                <Pill icon={<Home className="h-4 w-4" />} text="Hogar" />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* PRODUCTS */}
      <section className="mx-auto max-w-6xl px-5 py-10">
        <h2 className="text-2xl font-semibold tracking-tight">Coberturas principales</h2>
        <p className="mt-2 text-zinc-600">Empezamos por lo importante y afinamos según tu caso.</p>

        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <Card icon={<HeartPulse className="h-5 w-5" />} title="Salud" text="Con o sin copago, cuadro médico, reembolso si aplica y condiciones por preexistencias." />
          <Card icon={<ShieldCheck className="h-5 w-5" />} title="Vida" text="Capitales, beneficiarios, coberturas extra y cómo elegir el nivel correcto." />
          <Card icon={<Home className="h-5 w-5" />} title="Hogar" text="Continente/contenido, daños por agua, robo, responsabilidad civil y asistencia." />
        </div>
      </section>

      {/* FAQ */}
      <section className="mx-auto max-w-6xl px-5 py-10">
        <h2 className="text-2xl font-semibold tracking-tight">Preguntas típicas</h2>
        <div className="mt-6 grid gap-3 md:grid-cols-2">
          <Faq q="¿Me vas a preguntar por copagos y preexistencias?" a="Sí. Son de las preguntas clave para afinar el precio y evitar sorpresas." />
          <Faq q="¿Cuánto se tarda?" a="Normalmente en minutos ya tengo lo básico. Luego seguimos por WhatsApp con la propuesta." />
          <Faq q="¿Puedo pedir que priorices hospitalización o reembolso?" a="Claro. Me dices tu prioridad y lo dejamos por escrito en la propuesta." />
          <Faq q="¿Puedo hacerlo todo por WhatsApp?" a="Sí. La web es solo la puerta bonita. El cierre lo hacemos por WhatsApp." />
        </div>
      </section>

      {/* FORM */}
      <section id="form" className="mx-auto max-w-6xl px-5 py-10">
        <div className="rounded-3xl border border-zinc-200 bg-zinc-50 p-6 md:p-10">
          <h2 className="text-2xl font-semibold tracking-tight">Envíame tus datos en 1 minuto</h2>
          <p className="mt-2 text-zinc-600">Y te preparo la propuesta para que la tengas lista por WhatsApp.</p>

          <form className="mt-6 grid gap-3 md:grid-cols-2" action="/api/lead" method="post">
            <input name="name" placeholder="Nombre" className="h-11 rounded-xl border border-zinc-200 px-4 outline-none focus:ring-2 focus:ring-zinc-300" />
            <input name="phone" placeholder="Teléfono (WhatsApp)" className="h-11 rounded-xl border border-zinc-200 px-4 outline-none focus:ring-2 focus:ring-zinc-300" />
            <input name="province" placeholder="Provincia / Ciudad" className="h-11 rounded-xl border border-zinc-200 px-4 outline-none focus:ring-2 focus:ring-zinc-300" />
            <select name="product" className="h-11 rounded-xl border border-zinc-200 px-4 outline-none focus:ring-2 focus:ring-zinc-300">
              <option value="salud">Salud</option>
              <option value="vida">Vida</option>
              <option value="hogar">Hogar</option>
            </select>
            <textarea name="notes" placeholder="Algo importante (edades, copago, preexistencias, etc.)" className="md:col-span-2 min-h-28 rounded-xl border border-zinc-200 p-4 outline-none focus:ring-2 focus:ring-zinc-300" />
            <button className="md:col-span-2 h-11 rounded-xl bg-zinc-900 text-white hover:bg-zinc-800 transition">
              Enviar
            </button>
          </form>

          <p className="mt-3 text-xs text-zinc-500">
            Al enviar, te contactaré para completar 2–3 detalles y dejarlo listo por WhatsApp.
          </p>
        </div>
      </section>

      <footer className="border-t border-zinc-100 py-8">
        <div className="mx-auto max-w-6xl px-5 text-sm text-zinc-500 flex items-center justify-between">
          <span>© {new Date().getFullYear()} Valentín Protección Integral</span>
          <a className="hover:text-zinc-700" href={WHATSAPP_LINK}>WhatsApp</a>
        </div>
      </footer>
    </main>
  );
}

function Badge({ icon, text }: { icon: React.ReactNode; text: string }) {
  return <div className="flex items-center gap-2 rounded-2xl border border-zinc-200 bg-white px-3 py-2">{icon}<span>{text}</span></div>;
}
function Step({ n, title, desc }: { n: string; title: string; desc: string }) {
  return (
    <li className="flex gap-3">
      <div className="h-7 w-7 rounded-full bg-zinc-900 text-white flex items-center justify-center text-xs">{n}</div>
      <div>
        <p className="font-medium">{title}</p>
        <p className="text-zinc-600">{desc}</p>
      </div>
    </li>
  );
}
function Pill({ icon, text }: { icon: React.ReactNode; text: string }) {
  return <span className="inline-flex items-center gap-2 rounded-full border border-zinc-200 bg-zinc-50 px-3 py-1.5 text-sm">{icon}{text}</span>;
}
function Card({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) {
  return (
    <div className="rounded-3xl border border-zinc-200 bg-white p-6 shadow-sm">
      <div className="flex items-center gap-2 text-zinc-900">{icon}<h3 className="font-semibold">{title}</h3></div>
      <p className="mt-2 text-sm text-zinc-600 leading-relaxed">{text}</p>
    </div>
  );
}
function Faq({ q, a }: { q: string; a: string }) {
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white p-5">
      <p className="font-medium">{q}</p>
      <p className="mt-2 text-sm text-zinc-600">{a}</p>
    </div>
  );
}