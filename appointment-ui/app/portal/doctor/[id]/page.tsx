"use client";

// Doctor profile page (foodpanda vendor-detail parity): photo, degrees,
// hospital/department, fees box, rating summary, published reviews, a
// next-slots preview, and "বুক করুন" into the existing chat wizard with the
// doctor pre-selected. Prewarms the LLM prompt for this clinic+doctor on
// mount so the booking greeting starts fast.

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Building2,
  CalendarDays,
  Clock,
  Pencil,
  Phone,
  Sparkles,
  Star,
} from "lucide-react";
import { avatarGradient, initialsOf } from "@/lib/avatar";
import { LangToggle, useLang } from "@/lib/i18n";
import VoiceCall from "@/components/portal/VoiceCall";
import RatingStars from "@/components/portal/RatingStars";
import ReviewModal from "@/components/portal/ReviewModal";
import BookingSheet from "@/components/portal/BookingSheet";
import {
  ChatSlot,
  DoctorDetail,
  MyReview,
  Review,
  doctorPhotoUrl,
  portalDoctorDetail,
  portalDoctorReviews,
  portalMyReview,
  prewarmChat,
} from "@/lib/api";

function FeeBox({ doc }: { doc: DoctorDetail }) {
  const { t } = useLang();
  const cell = (label: string, fee: number | null) => (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-surface/80 p-4 text-center">
      <div className="absolute inset-x-0 top-0 h-[2px] opacity-70" style={{ background: "var(--brand-grad)" }} />
      <p className="text-[11px] font-bold uppercase tracking-widest text-faint">{label}</p>
      <p className="mt-1 text-2xl font-extrabold text-fg">
        {fee != null
          ? <>{"৳"}{fee}</>
          : <span className="text-sm font-semibold text-faint">{t("notSet")}</span>}
      </p>
      <p className="text-[10px] text-faint">{t("perVisit")}</p>
    </div>
  );
  return (
    <div className="grid grid-cols-2 gap-3">
      {cell(t("newPatient"), doc.fee_new)}
      {cell(t("followUp"), doc.fee_followup)}
    </div>
  );
}

function ReviewRow({ review }: { review: Review }) {
  const { t, dateLocale } = useLang();
  const name = review.reviewer_name || t("patient");
  return (
    <div className="rounded-2xl border border-border bg-surface/80 p-4 animate-fade-in">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2.5">
          <span
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-extrabold text-white"
            style={{ background: avatarGradient(name) }}
          >
            {initialsOf(name)}
          </span>
          <span className="min-w-0">
            <p className="truncate text-sm font-bold text-fg">{name}</p>
            <p className="text-[11px] text-faint">
              {new Date(review.created_at).toLocaleDateString(dateLocale, { year: "numeric", month: "long", day: "numeric" })}
            </p>
          </span>
        </span>
        <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-amber-400/10 px-2 py-1">
          {[1, 2, 3, 4, 5].map((n) => (
            <Star key={n} size={11}
              className={n <= review.rating ? "fill-amber-400 text-amber-400" : "text-border"} />
          ))}
        </span>
      </div>
      {review.text && (
        <p className="mt-2.5 text-sm leading-relaxed text-muted" dir="auto">{review.text}</p>
      )}
    </div>
  );
}

export default function DoctorDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const doctorId = Number(id);
  const router = useRouter();

  const [doc, setDoc] = useState<DoctorDetail | null>(null);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [myReview, setMyReview] = useState<MyReview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  // Direct-booking sheet: {slot} = tapped chip; {slot: null} = main CTA
  // (pick a time inside the sheet). undefined = closed.
  const [sheetSlot, setSheetSlot] = useState<ChatSlot | null | undefined>(undefined);
  const { t } = useLang();

  useEffect(() => {
    if (!doctorId) return;
    (async () => {
      try {
        const d = await portalDoctorDetail(doctorId);
        setDoc(d);
        // Heat the LLM prompt cache for this clinic with the doctor
        // pre-selected — "বুক করুন" is the dominant next tap.
        prewarmChat(d.clinic_id, d.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : t("docLoadFailed"));
      } finally {
        setLoading(false);
      }
    })();
    portalDoctorReviews(doctorId).then(setReviews).catch(() => {});
    portalMyReview(doctorId).then(setMyReview).catch(() => {});
  }, [doctorId]);

  function book() {
    if (!doc) return;
    const p = new URLSearchParams({
      clinic: String(doc.clinic_id),
      hospital: doc.hospital_name,
      hospitalId: String(doc.hospital_id),
      department: doc.department_name,
      doctor: doc.name,
      doctorId: String(doc.id),
    });
    if (doc.degrees) p.set("degrees", doc.degrees);
    if (doc.specialty) p.set("specialty", doc.specialty);
    if (doc.fee_new != null) p.set("fee", String(doc.fee_new));
    router.push(`/portal/book?${p}`);
  }

  if (loading) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-bg">
        <div className="flex gap-1.5">
          {[0, 1, 2].map((i) => (
            <span key={i} className="h-2 w-2 animate-bounce rounded-full bg-primary"
              style={{ animationDelay: `${i * 150}ms` }} />
          ))}
        </div>
      </div>
    );
  }

  if (error || !doc) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-bg">
        <div className="card space-y-3 p-6 text-center">
          <p className="text-sm text-muted">{error || t("docNotFound")}</p>
          <Link href="/portal" className="btn-primary btn-sm">{t("back")}</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="relative min-h-screen w-full overflow-y-auto bg-bg">
      {/* Hero */}
      <div className="relative overflow-hidden" style={{ background: "var(--brand-grad)" }}>
        <div className="absolute inset-0" style={{ background: "linear-gradient(180deg, rgba(0,0,0,0.05) 0%, rgba(0,0,0,0.25) 100%)" }} />
        <div className="relative mx-auto w-full max-w-5xl px-5 pb-8 pt-5 lg:px-8">
          <div className="flex items-center justify-between">
            <Link href="/portal" aria-label={t("back")}
              className="inline-flex h-8 w-8 items-center justify-center rounded-xl bg-white/20 text-white backdrop-blur-sm transition hover:bg-white/30">
              <ArrowLeft size={14} />
            </Link>
            <LangToggle onDark />
          </div>

          <div className="mt-4 flex items-start gap-4">
            {doc.has_photo ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={doctorPhotoUrl(doc.id)} alt={doc.name}
                className="h-20 w-20 shrink-0 rounded-2xl object-cover shadow-xl ring-2 ring-white/40" />
            ) : (
              <span
                className="flex h-20 w-20 shrink-0 items-center justify-center rounded-2xl text-2xl font-extrabold tracking-wide text-white shadow-xl ring-2 ring-white/40"
                style={{ background: avatarGradient(doc.name) }}
              >
                {initialsOf(doc.name)}
              </span>
            )}
            <div className="min-w-0 flex-1">
              <h1 className="text-2xl font-extrabold tracking-tight text-white drop-shadow">{doc.name}</h1>
              {doc.degrees && <p className="mt-0.5 text-sm font-semibold text-white/85">{doc.degrees}</p>}
              {doc.specialty && <p className="mt-0.5 text-sm text-white/65">{doc.specialty}</p>}
              <p className="mt-1.5 flex items-center gap-1.5 text-xs text-white/70">
                <Building2 size={12} className="shrink-0" />
                {doc.hospital_name} · {doc.department_name}
              </p>
              <div className="mt-2 inline-flex rounded-full bg-white/20 px-2.5 py-1 backdrop-blur-sm">
                <RatingStars rating={doc.avg_rating} count={doc.review_count} />
              </div>
            </div>
          </div>
        </div>
      </div>

      <main className="relative mx-auto w-full max-w-5xl px-5 pb-14 pt-6 lg:px-8">
        <div className="grid gap-8 lg:grid-cols-[1fr_380px] lg:items-start">
          {/* Left: fees, bio, slots, CTAs */}
          <div className="space-y-5">
            <FeeBox doc={doc} />

            {doc.description && (
              <p className="text-sm leading-relaxed text-muted" dir="auto">{doc.description}</p>
            )}

            {/* Slot preview */}
            <section className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
                <p className="text-xs font-bold uppercase tracking-widest text-faint">{t("nextSlots")}</p>
              </div>
              {doc.slots.length === 0 ? (
                <p className="text-sm text-faint">{t("noSlots7d")}</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {doc.slots.map((s) => (
                    <button key={s.datetime} onClick={() => setSheetSlot(s)}
                      className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface px-3 py-2 text-sm text-fg shadow-sm transition-colors hover:border-primary/40 hover:bg-[var(--brand-soft)]">
                      <Clock size={13} className="text-primary" />
                      <span dir="auto">{s.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>

            {/* Book CTA — direct booking sheet (slot picked inside);
                falls back to the AI chat when no slots are open. */}
            <button onClick={() => (doc.slots.length ? setSheetSlot(null) : book())}
              className="group relative w-full overflow-hidden rounded-2xl p-[1.5px] shadow-[0_0_30px_-5px_rgba(99,102,241,0.6)] transition-all hover:shadow-[0_0_50px_-5px_rgba(99,102,241,0.8)] active:scale-[0.98]"
              style={{ background: "var(--brand-grad)" }}>
              <div className="flex w-full items-center justify-center gap-3 rounded-2xl bg-surface px-6 py-4 transition-all duration-300 group-hover:bg-transparent">
                <CalendarDays size={17} className="text-indigo-300 transition group-hover:text-white" />
                <span className="text-sm font-bold text-fg transition group-hover:text-white">{t("bookNow")}</span>
              </div>
            </button>

            <button onClick={book}
              className="flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-surface/80 px-6 py-3 text-sm font-semibold text-fg backdrop-blur-sm transition hover:border-indigo-500/40 active:scale-[0.98]">
              <Sparkles size={15} className="text-primary" />
              {t("bsWithAI")}
            </button>

            <button onClick={() => setVoiceOpen(true)}
              className="flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-surface/80 px-6 py-3 text-sm font-semibold text-fg backdrop-blur-sm transition hover:border-indigo-500/40 active:scale-[0.98]">
              <Phone size={15} className="text-primary" />
              {t("bookByVoice")}
            </button>
          </div>

          {/* Right: reviews */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="h-5 w-1 rounded-full" style={{ background: "var(--brand-grad)" }} />
                <p className="text-xs font-bold uppercase tracking-widest text-faint">
                  {t("reviewsHeading")} {doc.review_count > 0 ? `(${doc.review_count})` : ""}
                </p>
              </div>
              {myReview && (
                <button onClick={() => setReviewOpen(true)}
                  className="btn-ghost flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-semibold text-primary">
                  <Pencil size={12} /> {t("editMyReview")}
                </button>
              )}
            </div>
            {reviews.length === 0 ? (
              <div className="rounded-2xl border border-border/50 bg-surface/50 p-8 text-center">
                <p className="text-sm font-semibold text-muted">{t("noReviewsTitle")}</p>
                <p className="mt-1 text-xs text-faint">{t("noReviewsSub")}</p>
              </div>
            ) : (
              <div className="space-y-3">
                {reviews.map((r) => <ReviewRow key={r.id} review={r} />)}
              </div>
            )}
          </section>
        </div>
      </main>

      {voiceOpen && (
        <VoiceCall
          clinicId={doc.clinic_id}
          doctorId={doc.id}
          label={doc.name}
          onClose={() => setVoiceOpen(false)}
        />
      )}

      {sheetSlot !== undefined && (
        <BookingSheet
          doc={doc}
          slot={sheetSlot}
          onClose={() => setSheetSlot(undefined)}
          onBookWithAI={book}
          onBooked={() =>
            // A slot was consumed — refresh the page's preview list.
            portalDoctorDetail(doc.id).then(setDoc).catch(() => {})
          }
        />
      )}

      {reviewOpen && (
        <ReviewModal
          doctorId={doc.id}
          doctorName={doc.name}
          existing={myReview}
          onClose={() => setReviewOpen(false)}
          onSaved={(r) => {
            setMyReview(r);
            portalDoctorReviews(doc.id).then(setReviews).catch(() => {});
          }}
        />
      )}
    </div>
  );
}
