export const INSURERS = [
  {
    id: "nextcare",
    label: "NextCare",
    payerPattern: /NEXTCARE/i,
    urlPatterns: ["https://pulse-uae.nextcarehealth.com/*"],
    loginUrl: "https://pulse-uae.nextcarehealth.com/Login2.aspx?token=AQ==",
    types: [
      "Out-Patient",
      "In-Patient",
      "Dental",
      "Optical",
      "Maternity",
      "Emergency Room Services",
      "Chronic Out",
      "Life",
      "Cobra",
      "Travel Insurance",
      "Collision Damage Waiver Excess",
    ],
    defaultType: "Out-Patient",
  },
  {
    id: "mednet",
    label: "MedNet",
    payerPattern: /MEDNET|MEDGULF|MED\s*GULF/i,
    urlPatterns: ["https://mednetapprovals.com/*"],
    loginUrl: "https://mednetapprovals.com/MedNeXtOnline/faces/pages/login.jspx",
  },
  {
    id: "liva",
    label: "Liva (NLI Gulf)",
    payerPattern: /LIVA|NLIC\s*GULF/i,
    urlPatterns: ["https://online.nlicgulf.com:12443/*"],
    loginUrl: "https://online.nlicgulf.com:12443/remote/login?lang=en",
  },
  {
    id: "gig",
    label: "GIG (Direct Billing)",
    kind: "html-form",
    payerPattern: /AXA|GIG/i,
    urlPatterns: [],
    loginUrl: null,
    types: ["Outpatient", "Inpatient", "Emergency", "Maternity", "Dental", "Optical"],
    defaultType: "Outpatient",
    templateName: "GIG-form.html",
    templateUrl: "http://maxcall.local:3001/ext/forms/GIG-form.html",
  },
  {
    id: "oqic",
    label: "OQIC (Direct Billing — PDF)",
    kind: "static-pdf",
    payerPattern: /OQIC|OMAN\s*QATAR/i,
    urlPatterns: [],
    loginUrl: null,
    templateName: "OQIC-Claim-Form.pdf",
    templateUrl: "http://maxcall.local:3001/ext/forms/OQIC-Claim-Form.pdf",
  },
  {
    id: "vipul",
    label: "Vipul (Excel form)",
    kind: "html-form",
    payerPattern: /VIPUL/i,
    urlPatterns: [],
    loginUrl: null,
    templateName: "Vipul-form.html",
    templateUrl: "http://maxcall.local:3001/ext/forms/Vipul-form.html",
  },
];

export function detectInsurer(payerName) {
  // مرونة التسمية: "NEXT CARE" / "Next-Care" / "NEXTCARE" كلها تطابق —
  // نطبّع الاسم بإزالة الفواصل والفراغات قبل المطابقة
  const norm = String(payerName || "").replace(/[\s\-_.]/g, "").toUpperCase();
  if (!norm) return null;
  return INSURERS.find((i) => i.payerPattern.test(norm)) || null;
}
