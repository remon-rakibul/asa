"use client";

// Lightweight patient-portal i18n: Bangla-first with an English toggle,
// persisted per browser. Covers UI chrome only — the AI agent's replies are
// LLM-composed and follow the patient's own language in conversation.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export type Lang = "bn" | "en";

const LS_KEY = "asa_portal_lang";

const STRINGS = {
  // ── nav / shared ────────────────────────────────────────────────
  goodMorning: { bn: "শুভ সকাল", en: "Good morning" },
  goodAfternoon: { bn: "শুভ অপরাহ্ন", en: "Good afternoon" },
  goodEvening: { bn: "শুভ সন্ধ্যা", en: "Good evening" },
  myAppointments: { bn: "আমার অ্যাপয়েন্টমেন্ট", en: "My appointments" },
  signOut: { bn: "সাইন আউট", en: "Sign out" },
  back: { bn: "ফিরে যান", en: "Go back" },
  loading: { bn: "লোড হচ্ছে…", en: "Loading…" },

  // ── home hero ───────────────────────────────────────────────────
  heroTitle1: { bn: "সঠিক ডাক্তার খুঁজুন,", en: "Find the right doctor," },
  heroTitle2: { bn: " এক মিনিটে বুক করুন", en: " book in a minute" },
  heroSub: {
    bn: "সব হাসপাতালের ডাক্তার এক জায়গায় — ফি, রোগীদের রেটিং ও খালি সময় দেখে তুলনা করুন, পছন্দ হলে সাথে সাথে অ্যাপয়েন্টমেন্ট নিন।",
    en: "Every hospital's doctors in one place — compare fees, patient ratings and open slots, then book instantly.",
  },
  statDoctors: { bn: "{n} জন ডাক্তার", en: "{n} doctors" },
  statDoctorOne: { bn: "১ জন ডাক্তার", en: "1 doctor" },
  statHospitals: { bn: "{n} হাসপাতাল", en: "{n} hospitals" },
  statSpecialties: { bn: "{n} বিশেষত্ব", en: "{n} specialties" },
  searchPlaceholder: {
    bn: "ডাক্তার, বিশেষত্ব বা হাসপাতাল খুঁজুন…",
    en: "Search doctors, specialties or hospitals…",
  },
  brandName: { bn: "ASA", en: "ASA" },
  brandTagline: { bn: "অ্যাপয়েন্টমেন্ট সেটার এজেন্ট", en: "Appointment Setter Agent" },
  aiTitle: { bn: "ASA সহকারী", en: "ASA Assistant" },
  aiOnline: { bn: "এখনই অনলাইনে", en: "Online now" },
  aiPitch: {
    bn: "বাংলায় বলুন কী সমস্যা — সেরা ডাক্তার খুঁজে দেবে, ফি জানাবে, বুকিংও করে দেবে।",
    en: "Describe your problem in Bangla or English — it finds the best doctor, quotes fees and books for you.",
  },
  chatNow: { bn: "চ্যাট করুন", en: "Chat now" },
  voiceCall: { bn: "ভয়েস কল", en: "Voice call" },
  how1Title: { bn: "খুঁজুন", en: "Search" },
  how1Text: {
    bn: "নাম, বিশেষত্ব বা হাসপাতাল লিখে — কিংবা AI-কে বাংলায় বলে",
    en: "By name, specialty or hospital — or just ask the AI",
  },
  how2Title: { bn: "তুলনা করুন", en: "Compare" },
  how2Text: {
    bn: "ফি, রোগীদের রেটিং ও সবচেয়ে কাছের খালি সময় পাশাপাশি দেখে",
    en: "Fees, patient ratings and the soonest open slot, side by side",
  },
  how3Title: { bn: "বুক করুন", en: "Book" },
  how3Text: {
    bn: "পছন্দের সময়ে এক ট্যাপে — নিশ্চিতকরণ SMS চলে যাবে",
    en: "One tap on your preferred time — confirmation SMS included",
  },
  bySpecialty: { bn: "বিশেষত্ব অনুযায়ী", en: "By specialty" },
  doctorsHeading: { bn: "ডাক্তারসমূহ", en: "Doctors" },
  doctorsCount: { bn: "· {n} জন", en: "· {n}" },
  noDoctorsTitle: { bn: "কোনো ডাক্তার পাওয়া যায়নি", en: "No doctors found" },
  noDoctorsSub: {
    bn: "অন্য নাম বা বিশেষত্ব দিয়ে খুঁজে দেখুন — অথবা AI সহকারীকে জিজ্ঞেস করুন।",
    en: "Try a different name or specialty — or ask the AI assistant.",
  },
  loadMore: { bn: "আরও দেখুন", en: "Load more" },
  browseByHospital: { bn: "হাসপাতাল অনুযায়ী ব্রাউজ করুন", en: "Browse by hospital" },
  searchFailed: { bn: "ডাক্তার খোঁজা যায়নি।", en: "Search failed." },

  // ── filters ─────────────────────────────────────────────────────
  sortRating: { bn: "সেরা রেটিং", en: "Top rated" },
  sortAvailable: { bn: "দ্রুত পাওয়া যায়", en: "Soonest free" },
  sortFee: { bn: "কম ফি", en: "Lowest fee" },
  allHospitals: { bn: "সব হাসপাতাল", en: "All hospitals" },
  hospital: { bn: "হাসপাতাল", en: "Hospital" },
  maxFee: { bn: "সর্বোচ্চ ফি ৳", en: "Max fee ৳" },

  // ── doctor card / ratings ───────────────────────────────────────
  feeNotSet: { bn: "ফি নির্ধারিত হয়নি", en: "Fee not set" },
  followUpFee: { bn: "ফলো-আপ ৳{n}", en: "follow-up ৳{n}" },
  newBadge: { bn: "নতুন", en: "New" },
  rating: { bn: "রেটিং", en: "Rating" },
  stars: { bn: "{n} তারা", en: "{n} stars" },

  // ── doctor detail ───────────────────────────────────────────────
  newPatient: { bn: "নতুন রোগী", en: "New patient" },
  followUp: { bn: "ফলো-আপ", en: "Follow-up" },
  perVisit: { bn: "প্রতি ভিজিট", en: "per visit" },
  notSet: { bn: "নির্ধারিত হয়নি", en: "Not set" },
  nextSlots: { bn: "পরবর্তী খালি সময়", en: "Next available" },
  noSlots7d: {
    bn: "আগামী ৭ দিনে কোনো খালি সময় নেই — AI সহকারী বিকল্প খুঁজে দিতে পারবে।",
    en: "No open slots in the next 7 days — the AI assistant can find alternatives.",
  },
  bookNow: { bn: "বুক করুন", en: "Book now" },
  bookByVoice: { bn: "ফোনে কথা বলে বুক করুন", en: "Book by voice call" },
  reviewsHeading: { bn: "রিভিউ", en: "Reviews" },
  editMyReview: { bn: "আপনার রিভিউ সম্পাদনা করুন", en: "Edit your review" },
  noReviewsTitle: { bn: "এখনো কোনো রিভিউ নেই", en: "No reviews yet" },
  noReviewsSub: {
    bn: "ভিজিটের পর আপনিই প্রথম রিভিউ দিতে পারবেন।",
    en: "After your visit, you can be the first to review.",
  },
  docNotFound: { bn: "ডাক্তার পাওয়া যায়নি।", en: "Doctor not found." },
  docLoadFailed: { bn: "ডাক্তারের তথ্য পাওয়া যায়নি।", en: "Couldn't load doctor details." },
  patient: { bn: "রোগী", en: "Patient" },

  // ── review modal ────────────────────────────────────────────────
  writeReview: { bn: "রিভিউ দিন", en: "Write a review" },
  editReview: { bn: "রিভিউ সম্পাদনা করুন", en: "Edit review" },
  reviewPlaceholder: { bn: "আপনার অভিজ্ঞতা লিখুন (ঐচ্ছিক)…", en: "Share your experience (optional)…" },
  saveReview: { bn: "রিভিউ জমা দিন", en: "Submit review" },
  updateReview: { bn: "আপডেট করুন", en: "Update" },
  saving: { bn: "সংরক্ষণ হচ্ছে…", en: "Saving…" },
  reviewFailed: { bn: "রিভিউ সংরক্ষণ করা যায়নি। আবার চেষ্টা করুন।", en: "Couldn't save the review. Please try again." },
  close: { bn: "বন্ধ করুন", en: "Close" },
  reviewAbout: { bn: "ডা. {name} সম্পর্কে রিভিউ", en: "Review of Dr. {name}" },

  // ── appointments page ───────────────────────────────────────────
  apptsTitle: { bn: "আমার অ্যাপয়েন্টমেন্ট", en: "My Appointments" },
  apptsSub: { bn: "আপনার আসন্ন ও আগের সব বুকিং", en: "All your upcoming and past bookings" },
  newAppointment: { bn: "নতুন অ্যাপয়েন্টমেন্ট", en: "New appointment" },
  upcoming: { bn: "আসন্ন", en: "Upcoming" },
  pastCancelled: { bn: "আগের ও বাতিল", en: "Past & cancelled" },
  noAppts: { bn: "এখনো কোনো অ্যাপয়েন্টমেন্ট নেই", en: "No appointments yet" },
  noApptsSub: { bn: "আপনার প্রথম অ্যাপয়েন্টমেন্ট বুক করুন।", en: "Book your first appointment with a doctor." },
  bookNowCta: { bn: "এখনই বুক করুন", en: "Book now" },
  addToCalendar: { bn: "ক্যালেন্ডারে যোগ করুন", en: "Add to calendar" },
  reschedule: { bn: "রিশিডিউল", en: "Reschedule" },
  cancel: { bn: "বাতিল করুন", en: "Cancel" },
  serialN: { bn: "সিরিয়াল #{n}", en: "Serial #{n}" },
  today: { bn: "আজ", en: "Today" },
  tomorrow: { bn: "আগামীকাল", en: "Tomorrow" },
  yesterday: { bn: "গতকাল", en: "Yesterday" },
  inDays: { bn: "{n} দিন পরে", en: "in {n} days" },
  daysAgo: { bn: "{n} দিন আগে", en: "{n} days ago" },
  statusConfirmed: { bn: "নিশ্চিত", en: "Confirmed" },
  statusCompleted: { bn: "সম্পন্ন", en: "Completed" },
  statusCheckedIn: { bn: "চেক-ইন", en: "Checked in" },
  statusCancelled: { bn: "বাতিল", en: "Cancelled" },
  statusNoShow: { bn: "অনুপস্থিত", en: "No show" },
  apptsLoadFailed: { bn: "অ্যাপয়েন্টমেন্ট লোড করা যায়নি।", en: "Could not load appointments." },
  apptCancelled: { bn: "অ্যাপয়েন্টমেন্ট বাতিল হয়েছে", en: "Appointment cancelled" },
  apptCancelFailed: { bn: "অ্যাপয়েন্টমেন্ট বাতিল করা যায়নি", en: "Could not cancel appointment" },
  cancelApptTitle: { bn: "অ্যাপয়েন্টমেন্ট বাতিল করুন", en: "Cancel appointment" },
  cancelApptDesc: {
    bn: "আপনি কি নিশ্চিত এই অ্যাপয়েন্টমেন্টটি বাতিল করতে চান? এটি আর ফেরানো যাবে না।",
    en: "Are you sure you want to cancel this appointment? This cannot be undone.",
  },
  keepIt: { bn: "রেখে দিন", en: "Keep it" },
  cancelling: { bn: "বাতিল হচ্ছে…", en: "Cancelling…" },
  rescheduleTitle: { bn: "অ্যাপয়েন্টমেন্ট রিশিডিউল করুন", en: "Reschedule appointment" },
  apptRescheduled: { bn: "অ্যাপয়েন্টমেন্ট রিশিডিউল হয়েছে", en: "Appointment rescheduled" },
  reviewSaved: { bn: "রিভিউ সংরক্ষণ হয়েছে", en: "Review saved" },
  doctorFallback: { bn: "ডাক্তার", en: "Doctor" },

  // ── chat (book) page chrome ─────────────────────────────────────
  bookingHeader: { bn: "অ্যাপয়েন্টমেন্ট বুকিং", en: "Book appointment" },
  chatPlaceholder: { bn: "বাংলা বা ইংরেজিতে লিখুন…", en: "Type in Bangla or English…" },
  thinking: { bn: "ভাবছি…", en: "Thinking…" },
  sessionExpired: {
    bn: "আপনার সেশনের মেয়াদ শেষ হয়ে গেছে — অনুগ্রহ করে আবার লগইন করুন।",
    en: "Your session has expired — please log in again.",
  },
  connectionError: { bn: "সংযোগে সমস্যা হয়েছে। আবার চেষ্টা করুন।", en: "Connection problem. Please try again." },
  speakVoice: { bn: "ভয়েসে কথা বলুন", en: "Talk by voice" },
  newConversation: { bn: "নতুন কথোপকথন শুরু করুন", en: "Start a new conversation" },
  stop: { bn: "থামুন", en: "Stop" },

  // ── direct booking sheet ────────────────────────────────────────
  bsSlot: { bn: "সময় বেছে নিন", en: "Pick a time" },
  bsFee: { bn: "ভিজিট ফি", en: "Visit fee" },
  bsName: { bn: "রোগীর নাম", en: "Patient name" },
  bsAge: { bn: "বয়স", en: "Age" },
  bsPhone: { bn: "মোবাইল নম্বর", en: "Mobile number" },
  bsConfirm: { bn: "বুকিং নিশ্চিত করুন", en: "Confirm booking" },
  bsBooking: { bn: "বুক হচ্ছে…", en: "Booking…" },
  bsSuccessTitle: { bn: "অ্যাপয়েন্টমেন্ট বুক হয়েছে!", en: "Appointment booked!" },
  bsSerial: { bn: "সিরিয়াল", en: "Serial" },
  bsViewAppts: { bn: "আমার অ্যাপয়েন্টমেন্টে দেখুন", en: "View in my appointments" },
  bsSlotTaken: {
    bn: "দুঃখিত, এই সময়টি এইমাত্র অন্য কেউ নিয়ে নিয়েছেন।",
    en: "Sorry, that time was just taken by someone else.",
  },
  bsRefreshSlots: { bn: "নতুন সময় দেখুন", en: "See new times" },
  bsWithAI: { bn: "AI অ্যাসিস্ট্যান্ট দিয়ে বুক করুন", en: "Book with the AI assistant" },
  bsCancel: { bn: "বন্ধ করুন", en: "Close" },
  bsAgeInvalid: { bn: "১–১২০ এর মধ্যে দিন", en: "Enter 1–120" },
  bsPhoneInvalid: { bn: "১০–১১ সংখ্যার নম্বর দিন", en: "Enter a 10–11 digit number" },
  bsFailed: { bn: "বুকিং করা যায়নি। আবার চেষ্টা করুন।", en: "Couldn't book. Please try again." },

  // ── floating assistant (chat/voice bubbles) ─────────────────────
  faChat: { bn: "AI সহকারীর সাথে চ্যাট করুন", en: "Chat with the AI assistant" },
  faVoice: { bn: "ভয়েস কল করুন", en: "Start a voice call" },
  faClose: { bn: "বন্ধ করুন", en: "Close" },

  // ── booking-fee payment step ─────────────────────────────────────
  payFeeTitle: { bn: "বুকিং ফি পরিশোধ করুন", en: "Pay the booking fee" },
  payFeeBody: {
    bn: "নিশ্চিত করতে ৳{n} সার্ভিস ফি দিন — পেমেন্ট সম্পন্ন হলে সাথে সাথে বুকিং নিশ্চিত হবে।",
    en: "Pay a ৳{n} service fee to confirm — your booking locks in the moment payment succeeds.",
  },
  payHoldNote: { bn: "স্লটটি {m} মিনিট ধরে রাখা হয়েছে", en: "Your slot is held for {m}" },
  payNowCta: { bn: "এখন পরিশোধ করুন", en: "Pay now" },
  payExpired: { bn: "সময় শেষ — স্লটটি আর ধরে রাখা হয়নি।", en: "Time's up — the slot is no longer held." },
  payFailed: { bn: "পেমেন্ট ব্যর্থ হয়েছে। আবার চেষ্টা করুন।", en: "Payment failed. Please try again." },
  payAtDeskNote: { bn: "হাসপাতালে গিয়ে ফি দিতে পারবেন", en: "You can also pay the fee at the hospital desk" },
  paymentPendingBadge: { bn: "পেমেন্ট বাকি", en: "Payment pending" },
  paySuccess: { bn: "পেমেন্ট সম্পন্ন — বুকিং নিশ্চিত হয়েছে", en: "Payment received — booking confirmed" },
  payNowLink: { bn: "এখন পরিশোধ করুন", en: "Pay now" },
  payWaiting: { bn: "অপেক্ষা করছে…", en: "Waiting…" },

  // ── freemium / subscription ─────────────────────────────────────
  accountPlanTitle: { bn: "আপনার প্ল্যান", en: "Your plan" },
  accountTitle: { bn: "অ্যাকাউন্ট", en: "Account" },
  accountSub: { bn: "প্ল্যান ও সাবস্ক্রিপশন", en: "Plan & subscription" },
  planFree: { bn: "ফ্রি", en: "Free" },
  planPremium: { bn: "প্রিমিয়াম", en: "Premium" },
  planTrialBadge: { bn: "ফ্রি ট্রায়াল", en: "Free trial" },
  planTrialEndsIn: { bn: "ট্রায়াল শেষ {d} দিনে", en: "Trial ends in {d} days" },
  premiumUntil: { bn: "প্রিমিয়াম {date} পর্যন্ত", en: "Premium until {date}" },
  planFreeCapLine: {
    bn: "এই মাসে {used}/{cap} এআই বুকিং ব্যবহার হয়েছে",
    en: "{used}/{cap} AI bookings used this month",
  },
  planUnlimited: { bn: "সীমাহীন এআই বুকিং", en: "Unlimited AI bookings" },
  subPriceLine: { bn: "মাসে মাত্র ৳{n}", en: "Just ৳{n}/month" },
  upgradeCta: { bn: "প্রিমিয়ামে আপগ্রেড করুন", en: "Upgrade to Premium" },
  renewCta: { bn: "প্রিমিয়াম রিনিউ করুন", en: "Renew Premium" },
  subscribing: { bn: "প্রক্রিয়াকরণ হচ্ছে…", en: "Processing…" },
  subSuccess: { bn: "প্রিমিয়াম চালু হয়েছে — ধন্যবাদ!", en: "Premium activated — thank you!" },
  premiumPerks: {
    bn: "এআই ভয়েস কল, সীমাহীন এআই বুকিং, এসএমএস রিমাইন্ডার ও সম্পূর্ণ হিস্ট্রি",
    en: "AI voice calls, unlimited AI bookings, SMS reminders & full history",
  },
  historyLimitedNote: {
    bn: "শুধু সাম্প্রতিক {n}টি অ্যাপয়েন্টমেন্ট দেখানো হচ্ছে — সব দেখতে প্রিমিয়াম নিন।",
    en: "Showing your latest {n} appointments — upgrade to Premium to see them all.",
  },
  upgradeVoiceTitle: { bn: "এআই ভয়েস কল প্রিমিয়াম ফিচার", en: "AI voice calls are a Premium feature" },
  upgradeVoiceBody: {
    bn: "প্রিমিয়ামে আপগ্রেড করে ডাক্তারের সাথে ভয়েসে কথা বলুন।",
    en: "Upgrade to Premium to book by talking, hands-free.",
  },
  upgradeChatTitle: { bn: "এই মাসের ফ্রি এআই বুকিং শেষ", en: "You've used your free AI bookings" },
  upgradeChatBody: {
    bn: "প্রিমিয়ামে সীমাহীন এআই বুকিং — অথবা সরাসরি স্লট বেছে বুক করুন।",
    en: "Premium gives unlimited AI bookings — or pick a slot directly to book now.",
  },

  // ── phone verification (premium calling) ───────────────────────
  pvTitle: { bn: "ফোনে কল করে বুক করুন", en: "Book by phone call" },
  pvBody: {
    bn: "নম্বর একবার যাচাই করুন — এরপর আপনার নম্বর থেকে প্ল্যাটফর্ম নম্বরে কল করলেই এআই সহকারী পাবেন (প্রিমিয়াম/ট্রায়াল)।",
    en: "Verify your number once — then just call the platform number from it to reach the AI assistant (Premium/Trial).",
  },
  pvPhonePlaceholder: { bn: "01XXXXXXXXX", en: "01XXXXXXXXX" },
  pvSendCode: { bn: "কোড পাঠান", en: "Send code" },
  pvCodeSent: { bn: "এসএমএসে ৬ সংখ্যার কোড পাঠানো হয়েছে।", en: "A 6-digit code was sent by SMS." },
  pvCodePlaceholder: { bn: "৬ সংখ্যার কোড", en: "6-digit code" },
  pvConfirm: { bn: "যাচাই করুন", en: "Verify" },
  pvVerifiedBadge: { bn: "নম্বর যাচাইকৃত", en: "Number verified" },
  pvVerifiedLine: {
    bn: "{phone} যাচাই করা আছে — এই নম্বর থেকে কল করলেই চলবে।",
    en: "{phone} is verified — just call from this number.",
  },
  pvSuccess: { bn: "নম্বর যাচাই সম্পন্ন!", en: "Phone verified!" },
  pvFailed: { bn: "যাচাই ব্যর্থ — আবার চেষ্টা করুন।", en: "Verification failed — try again." },

  // ── voice call modal ────────────────────────────────────────────
  vcConnecting: { bn: "সংযোগ হচ্ছে…", en: "Connecting…" },
  vcInitializing: { bn: "প্রস্তুত হচ্ছে…", en: "Getting ready…" },
  vcListening: { bn: "শুনছি…", en: "Listening…" },
  vcThinking: { bn: "ভাবছি…", en: "Thinking…" },
  vcSpeaking: { bn: "বলছি…", en: "Speaking…" },
  vcDisconnected: { bn: "সংযোগ বিচ্ছিন্ন", en: "Disconnected" },
  vcFailed: { bn: "ভয়েস কল শুরু করা যায়নি।", en: "Couldn't start the voice call." },
  vcTitle: { bn: "ভয়েস কল", en: "Voice call" },
  vcMicTitle: { bn: "মাইক্রোফোন ব্যবহারের অনুমতি দিন", en: "Allow microphone access" },
  vcMicText: {
    bn: "ভয়েস কলের জন্য মাইক্রোফোন প্রয়োজন। ব্রাউজারের অ্যাড্রেস বারের পাশের আইকনে ক্লিক করে অনুমতি দিন, তারপর আবার চেষ্টা করুন।",
    en: "A microphone is required for voice calls. Click the icon beside the browser's address bar to allow access, then try again.",
  },
  vcStartSpeaking: { bn: "কথা বলা শুরু করুন…", en: "Start speaking…" },
  vcBooked: { bn: "বুক হয়েছে", en: "Booked" },
  vcEndCall: { bn: "কল শেষ করুন", en: "End call" },
  vcConfirmed: { bn: "অ্যাপয়েন্টমেন্ট নিশ্চিত হয়েছে", en: "Appointment confirmed" },
  vcSerialLabel: { bn: "সিরিয়াল নম্বর", en: "Serial number" },
  vcConnIssue: { bn: "সংযোগে সমস্যা হয়েছে।", en: "Connection problem." },

  // ── chat confirm cards / misc ───────────────────────────────────
  fee: { bn: "ফি", en: "Fee" },
  bookThisTime: { bn: "এই সময়ে বুক করবেন?", en: "Book this time?" },
  confirmBtn: { bn: "নিশ্চিত করুন", en: "Confirm" },
  changeBtn: { bn: "পরিবর্তন", en: "Change" },
  noBtn: { bn: "না", en: "No" },
  viewInAppts: { bn: "My Appointments-এ দেখুন", en: "View in My Appointments" },
  retryBtn: { bn: "আবার চেষ্টা করুন", en: "Try again" },
  drPrefix: { bn: "ডা.", en: "Dr." },
} as const;

export type StringKey = keyof typeof STRINGS;

type Ctx = {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: StringKey, vars?: Record<string, string | number>) => string;
  dateLocale: string;
};

const LangContext = createContext<Ctx | null>(null);

export function LangProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("bn");

  useEffect(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem(LS_KEY) : null;
    if (saved === "en" || saved === "bn") setLangState(saved);
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try { localStorage.setItem(LS_KEY, l); } catch { /* private mode */ }
  }, []);

  const t = useCallback(
    (key: StringKey, vars?: Record<string, string | number>) => {
      let s: string = STRINGS[key][lang];
      if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
      return s;
    },
    [lang],
  );

  const value = useMemo<Ctx>(
    () => ({ lang, setLang, t, dateLocale: lang === "bn" ? "bn-BD" : "en-GB" }),
    [lang, setLang, t],
  );

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): Ctx {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error("useLang must be used inside LangProvider");
  return ctx;
}

/** Compact EN/বাং switch — style adapts to gradient (onDark) or surface navs. */
export function LangToggle({ onDark = false }: { onDark?: boolean }) {
  const { lang, setLang } = useLang();
  const base = "rounded-lg px-2 py-1 text-[11px] font-bold transition";
  return (
    <div
      className={`flex items-center gap-0.5 rounded-xl p-0.5 ${
        onDark ? "bg-white/20 backdrop-blur-sm" : "border border-border bg-surface/80"
      }`}
      role="group"
      aria-label="Language"
    >
      <button
        onClick={() => setLang("bn")}
        aria-pressed={lang === "bn"}
        className={`${base} ${
          lang === "bn"
            ? onDark ? "bg-white text-indigo-700 shadow" : "bg-[var(--brand-soft)] text-primary"
            : onDark ? "text-white/75 hover:text-white" : "text-muted hover:text-fg"
        }`}
      >
        বাং
      </button>
      <button
        onClick={() => setLang("en")}
        aria-pressed={lang === "en"}
        className={`${base} ${
          lang === "en"
            ? onDark ? "bg-white text-indigo-700 shadow" : "bg-[var(--brand-soft)] text-primary"
            : onDark ? "text-white/75 hover:text-white" : "text-muted hover:text-fg"
        }`}
      >
        EN
      </button>
    </div>
  );
}
