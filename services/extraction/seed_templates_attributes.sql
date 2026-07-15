-- Data-only export from local ude.db (SQLite) for import into PostgreSQL.
-- Assumes the target schema already exists (created via this app's own
-- Base.metadata.create_all against the Postgres DATABASE_URL, same as
-- main.py does on startup) -- this file only inserts rows.
BEGIN;

-- attributes: 79 rows
INSERT INTO attributes (id, name, description, data_type, example) VALUES (1, 'MISC Code', 'The MISC (Malaysian Industry Standard Classification) code assigned to the entity, typically appearing as a 5-digit numeric code near the business/objects clause of the Company Act Section 14 statement. May be labelled ''MISC Code'', ''MSIC Code'' or appear beside the nature-of-business description. Extract the digits only, without any label or surrounding brackets.', 'alphanumeric', '12345');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (2, 'Business Description', 'The full narrative description of the company''s objects/business activities as stated in the Company Act Section 14 declaration. Usually one or more long paragraphs following a phrase such as ''the objects for which the company is established are''. Capture the entire paragraph(s) verbatim, preserving line breaks; do not summarise or truncate.', 'alphanumeric', 'To carry on the business of general cleaning of buildings, including the provision of ... (long text paragraph)');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (3, 'Entity Name', 'The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.', 'alphanumeric', 'AJS MAJU SERVICES SDN. BHD.');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (4, 'Business Registration Number', 'The company registration number issued by SSM, labelled as ''Registration No.'', ''No. Pendaftaran'', or ''Company No.'' Modern format is a 12-digit number followed by the legacy number in brackets, e.g. 201401032382(1108466-M). Older documents may show only the legacy format (e.g. 1108466-M). Extract the value exactly as printed including brackets and hyphens.', 'alphanumeric', '201401032382(1108466-M)');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (5, 'Incorporation Date', 'The date the company was incorporated, labelled as ''Date of Incorporation'', ''Tarikh Pemerbadanan'', or written in prose within the certificate (e.g. ''incorporated on the 10th day of September 2014''). Normalise to DD-MM-YYYY. If the document shows the date in long prose form, convert it.', 'datetime', '10-09-2024');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (6, 'Registered Address', 'The company''s registered office address, labelled ''Registered Address'' / ''Alamat Berdaftar''. Capture the full multi-line address block including lot/unit, building, street, postcode, city and state. Preserve line breaks as they appear; do not reformat into a single line and do not append the country unless printed.', 'alphanumeric', 'LOT 2-4-19
WISMA RAMPAI
TAMAN SRI RAMPAI, SETAPAK
KUALA LUMPUR
WILAYAH PERSEKUTUAN');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (7, 'Business Address', 'The address of the principal place of business, labelled ''Business Address'' / ''Alamat Perniagaan''. Capture the full multi-line block. If the form states it is the same as the registered office, extract the phrase as printed (e.g. ''Same as registered address'') rather than copying the registered address.', 'alphanumeric', 'Lot 2-4-19, Wisma Rampai, Taman Sri Rampai, 53300 Setapak, Kuala Lumpur');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (8, 'Business Nature', 'The stated nature of the company''s business, labelled ''Nature of Business'' / ''Sifat Perniagaan''. Usually a short phrase in capitals. Extract the phrase as printed. If multiple activities are listed as separate line items, extract each as a separate value.', 'alphanumeric', 'GENERAL CLEANING OF BUILDING');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (9, 'Shareholder Name', 'The full name of each shareholder / member listed in the shareholders table of Form 24, taken from the ''Name'' column. Extract every row in the table, including corporate shareholders. Preserve spelling exactly as printed, including abbreviations such as ''BIN'' / ''BN'' / ''BINTI''.', 'alphanumeric', 'MOHAMMAD FAIZ BN AHMDA NEWAZ');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (10, 'Shareholder Address', 'The residential or registered address of each shareholder, taken from the address column of the same table row as the shareholder name. Must stay aligned with the corresponding Shareholder Name so the pairing is preserved. Capture the full multi-line block.', 'alphanumeric', 'NO 29, JALAN AMAN SERENIA 11/7 ANIRA,
BANDAR SERENIA
MALAYSIA
43900, SEPANG
SELANGGOR');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (11, 'Shareholder Percentage', 'The shareholding held by each shareholder, labelled ''Total Share'' or ''Number of Shares'' in Form 24. In the source forms this is expressed as a share count (e.g. 18,000) rather than a percentage. Extract the value exactly as printed, retaining the thousands separator, and keep it aligned with the corresponding shareholder row.', 'alphanumeric', '18,000');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (12, 'Director Name', 'The full name of each director listed in Form 49, taken from the directors table. Extract every director row. Do not include company secretaries, managers, or auditors even where they appear in the same table - use the role/designation column to filter.', 'alphanumeric', 'MOHAMMAD FAIZ BIN AHMAD NEWAZ');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (13, 'Director Address', 'The residential address of each director, taken from the address column in the same table row as the director name. Must remain aligned with the corresponding Director Name. Capture the full multi-line block as printed.', 'alphanumeric', 'NO 29, JALAN AMAN SERENIA 11/7 ANIRA,
BANDAR SERENIA
43900, SEPANG
SELANGGOR');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (14, 'Director NRIC or Passport Number', 'The identification number of each director, labelled ''ID/PASSPORT/REGISTRATION NO.'' on Form 24 and ''IC/Passport'' on Form 49. Accepts two formats: (a) Malaysian NRIC of 12 digits with hyphens, e.g. 991207-08-6447; (b) an alphanumeric passport number, e.g. A12345678. Extract as printed, keeping hyphens for NRIC. Where both an old and new IC number are shown, extract the new (12-digit) number.', 'alphanumeric', '991207-08-6447');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (15, 'Financial Statement Date', 'The financial year end date for each comparative column of figures, taken from the column header of the Statement of Financial Position or Statement of Profit or Loss. Printed as ''Year ended 31 December 2023'', ''31.12.2023'', or simply ''2023'' above the RM column. Normalise to DD-MM-YYYY using the year end date. Extract one value per comparative column, left to right; this value keys every other figure in the same column.', 'datetime', '31-12-2023');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (16, 'Advances Due to Director', 'Amounts owed to or from directors, reported on the Statement of Financial Position and expanded in the related party notes. Usually sits under Current Liabilities as ''Amount due to directors'' or ''Directors'' loan account''. Where it instead appears under Current Assets as ''Amount due from directors'', store the value as a negative number. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Leave blank if the company reports no director advances; do not substitute zero. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '50000');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (17, 'Net Worth or Total Equity', 'Total shareholders'' equity from the Statement of Financial Position, labelled ''Total Equity'', ''Shareholders'' Funds'', or ''Net Worth''. Equals share capital plus retained earnings and reserves. If the statement shows an explicit ''Net Worth'' line separate from Total Equity, extract the Net Worth figure. May be negative where accumulated losses exceed share capital (capital deficiency). Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '1060425');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (18, 'Revenue or Turnover or Sales', 'The top line of the Statement of Profit or Loss. Extraction priority: (1) ''Revenue'', (2) ''Turnover'', (3) ''Sales''. Take the first of these that appears; do not fall back to Gross Profit for this field and never use Net Profit. Where revenue is disaggregated by segment in the notes, extract the consolidated total from the face of the statement. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '4820500');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (19, 'Costs or COGS', 'Direct cost of generating revenue, labelled ''Cost of Sales'', ''Cost of Goods Sold'', ''COGS'', or ''Direct Costs'' on the Statement of Profit or Loss. Presented as a deduction, often in parentheses; store as a positive number. Excludes operating expenses, finance costs and tax. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '3200000');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (20, 'Gross Profit', 'Gross profit from the Statement of Profit or Loss, labelled ''Gross Profit'' or ''Gross Loss''. Equals Revenue minus Cost of Sales. Where the statement is presented in a single-step format with no gross profit subtotal, leave blank rather than computing it. Negative if a gross loss is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '1620500');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (21, 'Expenses or Opex or SG&A or Overheads', 'Total operating expenses below the gross profit line, labelled ''Operating Expenses'', ''Administrative Expenses'', ''Selling and Distribution Expenses'', ''SG&A'', or ''Overheads''. Where several such lines are shown separately, sum them into a single value. Do NOT include Cost of Sales, Finance Costs, or Tax Expense. Store as a positive number even though it is presented as a deduction. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '816700');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (22, 'Operating Profit or EBIT', 'Profit from operations before finance costs and tax, labelled ''Operating Profit'', ''Profit from Operations'', or ''EBIT''. Equals Gross Profit plus Other Income minus Operating Expenses. Many Malaysian SME statements omit this subtotal; leave blank rather than computing it from components. Negative if an operating loss is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '803800');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (23, 'EBITDA', 'Earnings before interest, tax, depreciation and amortisation. Extract only where the statement explicitly presents an ''EBITDA'' line, which is uncommon in Malaysian statutory accounts. Do not derive it by adding back Depreciation & Amortisation to EBIT — leave blank if not explicitly stated, and let the downstream model compute it from the component fields. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '905300');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (24, 'Financing Cost', 'Interest and profit-sharing charges on borrowings, labelled ''Finance Costs'', ''Interest Expense'', or, for Islamic facilities, ''Profit Expense'' / ''Financing Cost''. Sits between Operating Profit and Profit Before Tax. Store as a positive number. Exclude interest income, which belongs in Other Income. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '197100');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (25, 'Depreciation & Amortisation', 'Depreciation of property, plant and equipment plus amortisation of intangible assets. Often not shown on the face of the Statement of Profit or Loss; take it from the ''Profit before tax is arrived at after charging'' note or the Statement of Cash Flows operating adjustments. Sum depreciation and amortisation into a single positive figure. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '101500');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (26, 'Other Income', 'Income from sources other than the principal revenue stream, labelled ''Other Income'', ''Other Operating Income'', or ''Sundry Income''. Includes interest income, rental income, and gains on disposal of assets. Presented as an addition; store as a positive number. Do not net it against expenses. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '0');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (27, 'Profit Before Tax', 'Profit before taxation from the Statement of Profit or Loss, labelled ''Profit Before Tax'', ''PBT'', or ''Profit Before Taxation''. For Islamic-compliant entities it may read ''Profit Before Zakat and Tax'' — extract that figure. Equals Operating Profit minus Finance Costs. Negative if a loss before tax is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '606700');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (28, 'Net Profit', 'Profit after tax, labelled ''Profit After Tax'', ''Profit for the Financial Year'', or ''Net Profit''. Where a Statement of Comprehensive Income follows, take the profit for the year line, not total comprehensive income. Negative if a loss for the year is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '461092');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (29, 'Asset Value or Total Current Assets', 'Total assets from the Statement of Financial Position, the ''TOTAL ASSETS'' line, being Total Non-Current Assets plus Total Current Assets. If no consolidated total is presented, extract Total Current Assets and flag the omission. Must satisfy Total Assets = Total Liabilities + Total Equity. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '2466400');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (30, 'Liability Value', 'Total liabilities from the Statement of Financial Position, being Total Non-Current Liabilities plus Total Current Liabilities. Many Malaysian statements omit an explicit ''TOTAL LIABILITIES'' line; where it is absent, sum the two subtotals. Do not derive it as Total Assets minus Total Equity unless neither subtotal is available. Store as a positive number. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.', 'numeric', '1405975');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (31, 'Balance Sheet Present', 'Whether the document contains a balance sheet. Accept any of the following headings as present: ''Statement of Financial Position'', ''Balance Sheet'', ''Penyata Kedudukan Kewangan''. Return TRUE if such a statement appears anywhere in the document, FALSE otherwise. This is a completeness check on the document, not a value extraction — a heading in the table of contents alone is not sufficient; the statement itself must be present.', 'boolean', 'TRUE');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (32, 'Profit and Loss Statement Present', 'Whether the document contains a profit and loss statement. Accept any of: ''Statement of Profit or Loss'', ''Statement of Profit or Loss and Other Comprehensive Income'', ''Statement of Comprehensive Income'', ''Income Statement'', ''Profit and Loss Account'', ''Penyata Pendapatan''. Return TRUE if the statement itself appears, FALSE otherwise.', 'boolean', 'TRUE');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (33, 'Cash Flow Statement Present', 'Whether the document contains a cash flow statement. Accept any of: ''Statement of Cash Flows'', ''Cash Flow Statement'', ''Penyata Aliran Tunai''. Return TRUE if the statement itself appears, FALSE otherwise. Note that companies filing unaudited or abridged accounts frequently omit this statement, so FALSE is a common and valid result.', 'boolean', 'FALSE');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (34, 'Auditor''s Report Present', 'Whether the document contains an independent auditor''s report. Accept any of: ''Independent Auditors'' Report'', ''Report of the Auditors'', ''Laporan Juruaudit Bebas''. Return TRUE only where a signed report from an external audit firm is present. A directors'' report, statutory declaration, or a statement by directors does not count. FALSE indicates the accounts are unaudited, which is material to the credit assessment.', 'boolean', 'TRUE');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (35, 'Bank Statement Month', 'The calendar month and year covered by one monthly statement, taken from the statement period header or the monthly summary block. Normalise to an English month name plus a four-digit year regardless of the source language. Malay month mapping: Januari=January, Februari=February, Mac=March, April=April, Mei=May, Jun=June, Julai=July, Ogos=August, September=September, Oktober=October, November=November, Disember=December. Extract the month and year only, not the full date range. A six-month bundle yields six values; this field keys the other three bank attributes.', 'alphanumeric', 'July 2023');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (36, 'Monthly Withdrawal', 'Total debits (withdrawals, payments, outflows) for the calendar month, taken from the monthly summary table and labelled ''Jumlah Debit / Total Debits'' (Maybank) or ''Total Debit'' (CIMB, RHB, Public Bank). This is the bank''s own aggregate figure — do not sum the individual transaction rows, which risks double counting reversals. Store as a positive number in RM, aligned to the Bank Statement Month of the same statement.', 'numeric', '340980');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (37, 'Monthly Deposit', 'Total credits (deposits, receipts, inflows) for the calendar month, taken from the monthly summary table and labelled ''Jumlah Kredit / Total Credits'' (Maybank) or ''Total Credit'' (CIMB, RHB, Public Bank). Use the bank''s aggregate figure, not the sum of transaction rows. Store as a positive number in RM, aligned to the Bank Statement Month of the same statement.', 'numeric', '362700');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (38, 'Monthly End Balance', 'The account balance on the last day of the calendar month, labelled ''Baki Akhir / Closing Balance'', ''Closing Balance'', or ''Balance c/f''. Should equal the opening balance of the following month, which is a useful continuity check across a six-month bundle. Negative if the account is overdrawn. Expressed in RM and aligned to the Bank Statement Month of the same statement.', 'numeric', '266750');
-- Bank Statements: daily-transactions shape (superseding attrs 35-38, left orphaned).
INSERT INTO attributes (id, name, description, data_type, example) VALUES (80, 'Bank Name', 'The issuing bank of this statement, from the statement header or footer (e.g. ''Maybank Berhad'', ''CIMB Bank Berhad'', ''RHB Bank Berhad'', ''Public Bank Berhad'', ''Standard Chartered Bank Malaysia Berhad''). Return the full bank name as printed. One value per statement.', 'alphanumeric', 'Standard Chartered Bank Malaysia Berhad');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (81, 'Account Number Masked', 'The account number this statement covers, from the header. Keep any masking the bank already applies (e.g. ''****4321''); never unmask or invent digits. One value per statement.', 'alphanumeric', '****4321');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (82, 'Statement Period', 'The date range the statement covers, from the period header (e.g. ''01 Jan 2026 to 30 Jun 2026''). Return as printed. One value per statement.', 'alphanumeric', '01 Jan 2026 to 30 Jun 2026');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (83, 'Transaction Date', 'The posting/value date of one transaction row, from the transaction listing. You MUST return it in strict ISO format YYYY-MM-DD and nothing else -- convert any printed form to ISO (e.g. ''23 Jan 2026'' -> ''2026-01-23'', ''23/01/2026'' -> ''2026-01-23''); never return the day-month-name form or a range. One row per printed transaction line, in the order printed.', 'datetime', '2026-01-23');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (84, 'Transaction Description', 'The narrative/description of one transaction row as printed (payee, reference, or transaction type).', 'alphanumeric', 'IBG TRANSFER TO SUPPLIER');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (85, 'Transaction Debit', 'The debit (money out / withdrawal / outflow) amount of one transaction row, as a positive number in RM. Null if the row is a credit rather than a debit.', 'numeric', '3500.00');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (86, 'Transaction Credit', 'The credit (money in / deposit / inflow) amount of one transaction row, as a positive number in RM. Null if the row is a debit rather than a credit.', 'numeric', '12000.00');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (87, 'Transaction Balance', 'The running account balance printed after one transaction row, in RM. Negative if the account is overdrawn.', 'numeric', '8500.00');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (39, 'Director Religion', 'The religion declared by a director, labelled ''Agama / Religion''. Typically a single word, and often a tick-box selection rather than free text: Islam, Buddha, Kristian, Hindu, Lain-lain. Extract the selected option as printed, without translating. Leave blank if no option is selected. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', 'Islam');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (40, 'Director Higher Education', 'The highest level of education attained by a director, labelled ''Pendidikan / Education'' or ''Taraf Pendidikan''. Usually a tick-box selection: SPM, STPM, Diploma, Ijazah / Degree, Sarjana / Master, PhD, Lain-lain. Where the form provides a free-text field for the institution or field of study, extract the qualification level only. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', 'Ijazah Sarjana Muda');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (41, 'Director Marital Status', 'The marital status of a director, labelled ''Status Perkahwinan / Marital Status''. A tick-box selection: Bujang / Single, Berkahwin / Married, and sometimes Duda / Janda (widowed or divorced). Normalise to the English term: Single, Married, Widowed, Divorced. Where the status is Married, the spouse fields should also be populated. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', 'Married');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (42, 'Director Spouse Name', 'The full name of a director''s spouse, labelled ''Nama Pasangan / Spouse Name''. Only present where the director''s marital status is Married. Leave blank for unmarried directors rather than repeating the director''s own name. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', 'Nurul Aina binti Abdullah');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (43, 'Director Spouse Contact Number', 'The contact telephone number of a director''s spouse, labelled ''No. Tel Pasangan''. Malaysian mobile format is 01X-XXX XXXX; landline is 0X-XXXX XXXX. Store exactly as written on the form, retaining spacing and hyphens. Do not prepend the +60 country code unless it is printed. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', '012-345 6789');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (44, 'Director Emergency Contact Name', 'The full name of the person a director nominates to be contacted in an emergency, labelled ''Nama Waris'' or ''Emergency Contact''. This is distinct from the spouse field, though the same person may be named in both. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', 'Zainab binti Hassan');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (45, 'Director Emergency Contact Number', 'The telephone number of a director''s nominated emergency contact, labelled ''No. Tel Waris''. Store exactly as written, retaining spacing and hyphens. Keep aligned with the Emergency Contact Name in the same block. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', '019-876 5432');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (46, 'Director Emergency Contact Relationship', 'The relationship between a director and the nominated emergency contact, labelled ''Hubungan / Relationship''. Free text or a tick box; common values are Isteri / Wife, Suami / Husband, Ibu / Mother, Bapa / Father, Adik-beradik / Sibling, Anak / Child. Extract as printed without translating. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', 'Ibu');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (47, 'Director Estimated Monthly Income', 'The director''s self-declared personal monthly income, labelled ''Anggaran Pendapatan Bulanan / Estimated Monthly Income''. Strip the RM prefix and any thousands separators and store as a number. This is personal income, not company revenue or the director''s drawings from the company. Where the form provides an income band rather than a figure, extract the lower bound and flag it. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'numeric', '8500');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (48, 'Director Email Address', 'The personal email address of a director, labelled ''E-mel / Email'' within the director information block. Store exactly as written; do not normalise to lower case. Distinct from the company email address, though a director may supply the same value for both. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'alphanumeric', 'faizal@gmail.com');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (49, 'Director Experience in Current Business', 'The number of years a director has worked in the same line of business as the applicant company, labelled ''Pengalaman dalam bidang / Years of Experience''. Extract the numeric count of years only, discarding the word ''years'' or ''tahun''. Where a range is given, extract the lower bound. Where the form asks for months, convert to years and round down. Note this counts experience in the industry, which may exceed the age of the company itself. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.', 'numeric', '12');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (50, 'Company Current Office Address', 'The address of the company''s current operating premises, labelled ''Alamat Perniagaan / Business Address'' or ''Alamat Operasi''. Capture the full multi-line block. This may differ from the registered office address on SSM Form 44; where the form states the two are the same, extract the phrase as printed rather than copying the registered address across.', 'alphanumeric', 'Lot 2-4-19, Wisma Rampai
Taman Sri Rampai
53300 Setapak
Kuala Lumpur');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (51, 'Company Office Status', 'Whether the company owns or rents its current office premises, labelled ''Status Premis''. A tick-box selection: Milik Sendiri / Owned, Sewa / Rented. Normalise to ''Owned'' or ''Rented''. Where the premises are rented, the monthly rent field should also be populated; where owned, the rent field is expected to be blank.', 'alphanumeric', 'Rented');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (52, 'Company Office Monthly Rent', 'The monthly rent paid on the company''s office premises, labelled ''Sewa Bulanan / Monthly Rent''. Strip the RM prefix and thousands separators and store as a number. Expected to be blank where Company Office Status is Owned; do not substitute zero, as a blank and a genuine zero carry different meanings for the credit assessment.', 'numeric', '3500');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (53, 'Company Age', 'The age of the company in years, labelled ''Umur Syarikat'' or ''Years in Operation''. Extract the numeric count of years only. Where the form leaves this blank, leave it blank rather than calculating it from the incorporation date; the downstream model can derive it. Where the form gives a range, extract the lower bound.', 'numeric', '10');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (54, 'Company Number of Staff', 'The headcount of the company, labelled ''Bilangan Pekerja / Number of Employees''. Extract the total as an integer. Where the form splits headcount into permanent and contract or into full-time and part-time, sum the categories into a single total. Where a range is given, extract the lower bound.', 'numeric', '25');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (55, 'Company Email Address', 'The company''s official contact email address, labelled ''E-mel Syarikat''. Store exactly as written; do not normalise to lower case. Where the form carries several email addresses, take the one in the company details section, not one from a director''s personal block.', 'alphanumeric', 'admin@ajsmaju.com.my');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (56, 'Company Office Telephone', 'The company''s office telephone number, labelled ''No. Tel Pejabat''. Malaysian landline format is 0X-XXXX XXXX; a mobile number may be given instead, in the format 01X-XXX XXXX. Store exactly as written on the form, retaining spacing and hyphens. Do not prepend the +60 country code unless it is printed.', 'alphanumeric', '03-4142 5678');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (57, 'Company Auditor Firm Name', 'The registered name of the audit firm engaged by the company, labelled ''Nama Firma Juruaudit / Auditor''. Typically ends in ''PLT'', ''& Co.'', or ''Chartered Accountants''. Extract the firm name only, excluding the firm''s address or AF registration number where those appear on the same line.', 'alphanumeric', 'Tan & Associates PLT');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (58, 'Company Auditor Contact Person', 'The name of the individual contact at the audit firm, labelled ''Pegawai Bertanggungjawab'' or ''Contact Person''. This is a person''s name, not the firm name. Leave blank where the form names only the firm.', 'alphanumeric', 'Tan Wei Ming');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (59, 'Company Auditor Contact Number', 'The telephone number of the audit firm or its named contact person, labelled ''No. Tel Juruaudit''. Store exactly as written, retaining spacing and hyphens. Where both a firm line and a mobile number are given, extract the number printed against the auditor contact fields.', 'alphanumeric', '03-7728 1234');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (60, 'Referrer', 'How the application reached the institution, captured as a closed set of exactly four values: Branch, Event, Personal, Other. This is a tick box or dropdown on the application form. Return one of the four values verbatim and nothing else. Where the selected option is Other, the Referrer Detail field carries the free text explanation. Do not infer the value from Referrer Detail if no option is selected.', 'alphanumeric', 'Branch');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (61, 'Referrer Detail', 'Free text elaborating on the Referrer selection: the branch name where Referrer is Branch, the event name and date where Referrer is Event, the introducing person''s name where Referrer is Personal, or an explanation where Referrer is Other. Extract the text exactly as written. Leave blank where the form provides no elaboration.', 'alphanumeric', 'Kota Bharu Branch');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (62, 'Main Contact Name', 'The full name of each person nominated as a point of contact for this application. Usually one or two people, and usually but not always directors. Extract every name listed in the contact section. Keep aligned with the corresponding Main Contact Email and Main Contact Phone Number in the same row or block.', 'alphanumeric', 'Mohammad Faiz bin Ahmad Newaz');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (63, 'Main Contact Email', 'The email address of each nominated contact person, aligned to the Main Contact Name in the same row. Store exactly as written; do not normalise to lower case. Where a contact has no email listed, leave that entry blank rather than shifting the remaining emails up, which would break the alignment with the names.', 'alphanumeric', 'faiz@ajsmaju.com.my');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (64, 'Main Contact Phone Number', 'The telephone number of each nominated contact person, aligned to the Main Contact Name in the same row. Store exactly as written, retaining spacing and hyphens. Where a contact has no number listed, leave that entry blank rather than shifting the remaining numbers up.', 'alphanumeric', '013-222 8899');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (65, 'Business Entity Type', 'The legal form of the applicant business, captured as a closed set of exactly three values: Sdn Bhd, Sole Prop, Partnership. This is a tick box or dropdown on the application form. Return one of the three values verbatim. Note the form may label this field ''Business Address'', which is a labelling error on the form itself; match on the available options rather than on the label.', 'alphanumeric', 'Sdn Bhd');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (66, 'Proposed Program', 'The financing programme or product the applicant is applying for, labelled ''Program / Skim Pembiayaan''. Extract the programme name exactly as printed, including any scheme code or Islamic contract name in brackets. Leave blank where the applicant has not nominated a programme.', 'alphanumeric', 'SME Working Capital Financing-i (Tawarruq)');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (67, 'Proposed Financing Amount', 'The gross financing amount requested by the applicant, labelled ''Jumlah Pembiayaan Dipohon / Financing Amount Requested''. Strip the RM prefix and thousands separators and store as a number. This is the facility amount applied for, not the net disbursement after fees, and not any amount subsequently approved.', 'numeric', '1200000');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (68, 'Additional Notes', 'Any free text the applicant or the receiving officer has written in the remarks, comments, or additional information section of the application form. Capture the full text verbatim, preserving line breaks. Do not summarise. Leave blank where the section is empty; do not fill it with text drawn from elsewhere on the form.', 'alphanumeric', 'Applicant requests expedited processing ahead of a Q4 contract award.');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (69, 'Legal Case Report', 'Each legal case disclosed in the litigation section of the CTOS report, covering both the company and its directors. Capture the full case entry as a single text block: case number, court, plaintiff, defendant, nature of the action, amount claimed, filing date, and status. Extract one value per case; a clean report has no cases, in which case return no values rather than a placeholder such as ''Nil'' or ''No records found''. Sections headed ''Bankruptcy'', ''Winding Up'', and ''Legal Suits'' all count as legal cases.', 'alphanumeric', 'Suit No. WA-B52-123-01/2023, Sessions Court Kuala Lumpur; Plaintiff: XYZ Supplies Sdn Bhd; Defendant: AJS Maju Services Sdn Bhd; Claim: RM45,200 for goods sold and delivered; Filed 12-01-2023; Status: Pending');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (70, 'MIA', 'Months in Arrears for a credit facility, reported in the CCRIS outstanding credit section as a twelve-digit string, one digit per month, most recent month first. Each digit is the number of months that instalment was in arrears; 0 means the facility was current. Extract the most recent month''s digit as the MIA for that facility, one value per facility, aligned to the same facility row as Limit, Outstanding and Instalment. A digit of 6 or above indicates an impaired account.', 'numeric', '0');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (71, 'Probability of Default', 'The probability of default score for the subject, reported on the CBM or credit bureau summary page as a percentage or a decimal. Where reported as a percentage, store the numeric value without the percent sign (e.g. 2.4 for 2.4%). Where a score band or grade accompanies the figure, extract the numeric probability only. There is one PD for the subject, not one per facility.', 'numeric', '2.4');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (72, 'Installment', 'The scheduled monthly instalment or repayment for a credit facility, reported in the CCRIS outstanding credit section. Store as a positive number in RM. One value per facility, aligned to the same facility row as Limit, Outstanding and MIA. Revolving facilities such as overdrafts and credit cards may report no instalment; leave those blank rather than entering zero.', 'numeric', '4820');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (73, 'Limit', 'The approved credit limit or original facility amount for a credit facility, reported in the CCRIS outstanding credit section. Store as a positive number in RM. One value per facility, aligned to the same facility row as Outstanding, Instalment and MIA. Term loans report the original approved amount; revolving facilities report the current limit.', 'numeric', '500000');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (74, 'Outstanding', 'The outstanding balance on a credit facility as at the CCRIS report date, reported in the outstanding credit section. Store as a positive number in RM. One value per facility, aligned to the same facility row as Limit, Instalment and MIA. May exceed the Limit where the facility is in excess.', 'numeric', '312450');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (75, 'Liabilities', 'Total credit exposure for the subject: the sum of outstanding balances across all facilities disclosed in the CCRIS report. Where the report prints an explicit total line, extract that figure. Otherwise leave blank rather than summing the facility rows, so that the total remains traceable to the document. Store as a positive number in RM.', 'numeric', '892300');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (76, 'Front Side IC Present', 'Whether the document contains a front side of the IC/MyKad.  Return TRUE if such the front side of the IC is in the document, FALSE otherwise. If what they upload is Passport then just return TRUE', 'boolean', 'True');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (77, 'Back Side IC Present', 'Whether the document contains a Back side of the IC/MyKad.  Return TRUE if such the back side of the IC is in the document, FALSE otherwise. If what they upload is Passport then just return TRUE', 'boolean', 'False');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (78, 'ID Type', 'Read the document and check what type of ID are provided. Return MyKad if its MyKad, Return Passport if its Passport. If none of two return Other', 'alphanumeric', 'MyKad');
INSERT INTO attributes (id, name, description, data_type, example) VALUES (79, 'Document Type', 'Return the document type. Only return what is on this list [Company Act Section 14, SSM Form 24, SSM Form 44, SSM Form 49, SSM Form 9 & 28, Form 32A, Financial Statements (Sdn Bhd), Borang B, Bank Statements, MyKad (Director ID or Passport), Consent Form, Customer Information Form, Application Details, CTOS Report, CCRIS / CBM Report, Other]', 'alphanumeric', 'SSM Form 24');

-- templates: 15 rows
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (1, 'Company Act Section 14', 'A statutory statement made under Section 14 of the Companies Act 2016, submitted at incorporation. Structured as running prose rather than a form with labelled fields, with the objects of the company set out in numbered clauses. Text-heavy and may span several pages; the fields of interest sit in the objects clause rather than in a header table. Document type: Constitution / Statutory Declaration.', 'Financial Application', 'You are extracting structured data from a "Company Act Section 14" document.
Document description: A statutory statement made under Section 14 of the Companies Act 2016, submitted at incorporation. Structured as running prose rather than a form with labelled fields, with the objects of the company set out in numbered clauses. Text-heavy and may span several pages; the fields of interest sit in the objects clause rather than in a header table. Document type: Constitution / Statutory Declaration.

Fields to extract:

1. MISC Code  |  Type: Alphanumeric — e.g. 12345
   The MISC (Malaysian Industry Standard Classification) code assigned to the entity, typically appearing as a 5-digit numeric code near the business/objects clause of the Company Act Section 14 statement. May be labelled ''MISC Code'', ''MSIC Code'' or appear beside the nature-of-business description. Extract the digits only, without any label or surrounding brackets.
2. Business Description  |  Type: Alphanumeric — e.g. To carry on the business of general cleaning of buildings, including the provision of ... (long text paragraph)
   The full narrative description of the company''s objects/business activities as stated in the Company Act Section 14 declaration. Usually one or more long paragraphs following a phrase such as ''the objects for which the company is established are''. Capture the entire paragraph(s) verbatim, preserving line breaks; do not summarise or truncate.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (2, 'SSM Form 24', 'SSM Form 24, filed to report an allotment of shares. Contains a company header block (name, registration number, incorporation date, registered address, nature of business) followed by a tabular list of allottees/shareholders with name, ID/passport number, address and number of shares. The shareholder table repeats one row per shareholder and may continue across pages. Document type: Return of Allotment of Shares.', 'Financial Application', 'You are extracting structured data from a "SSM Form 24" document.
Document description: SSM Form 24, filed to report an allotment of shares. Contains a company header block (name, registration number, incorporation date, registered address, nature of business) followed by a tabular list of allottees/shareholders with name, ID/passport number, address and number of shares. The shareholder table repeats one row per shareholder and may continue across pages. Document type: Return of Allotment of Shares.

Fields to extract:

1. Entity Name  |  Type: Alphanumeric — e.g. AJS MAJU SERVICES SDN. BHD.
   The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.
2. Business Registration Number  |  Type: Alphanumeric — e.g. 201401032382(1108466-M)
   The company registration number issued by SSM, labelled as ''Registration No.'', ''No. Pendaftaran'', or ''Company No.'' Modern format is a 12-digit number followed by the legacy number in brackets, e.g. 201401032382(1108466-M). Older documents may show only the legacy format (e.g. 1108466-M). Extract the value exactly as printed including brackets and hyphens.
3. Incorporation Date  |  Type: Datetime — e.g. 10-09-2024
   The date the company was incorporated, labelled as ''Date of Incorporation'', ''Tarikh Pemerbadanan'', or written in prose within the certificate (e.g. ''incorporated on the 10th day of September 2014''). Normalise to DD-MM-YYYY. If the document shows the date in long prose form, convert it.
4. Registered Address  |  Type: Alphanumeric — e.g. LOT 2-4-19
WISMA RAMPAI
TAMAN SRI RAMPAI, SETAPAK
KUALA LUMPUR
WILAYAH PERSEKUTUAN
   The company''s registered office address, labelled ''Registered Address'' / ''Alamat Berdaftar''. Capture the full multi-line address block including lot/unit, building, street, postcode, city and state. Preserve line breaks as they appear; do not reformat into a single line and do not append the country unless printed.
5. Business Nature  |  Type: Alphanumeric (multiple occurrences expected) — e.g. GENERAL CLEANING OF BUILDING
   The stated nature of the company''s business, labelled ''Nature of Business'' / ''Sifat Perniagaan''. Usually a short phrase in capitals. Extract the phrase as printed. If multiple activities are listed as separate line items, extract each as a separate value.
6. Shareholder Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. MOHAMMAD FAIZ BN AHMDA NEWAZ
   The full name of each shareholder / member listed in the shareholders table of Form 24, taken from the ''Name'' column. Extract every row in the table, including corporate shareholders. Preserve spelling exactly as printed, including abbreviations such as ''BIN'' / ''BN'' / ''BINTI''.
7. Shareholder Address  |  Type: Alphanumeric (multiple occurrences expected) — e.g. NO 29, JALAN AMAN SERENIA 11/7 ANIRA,
BANDAR SERENIA
MALAYSIA
43900, SEPANG
SELANGGOR
   The residential or registered address of each shareholder, taken from the address column of the same table row as the shareholder name. Must stay aligned with the corresponding Shareholder Name so the pairing is preserved. Capture the full multi-line block.
8. Shareholder Percentage  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 18,000
   The shareholding held by each shareholder, labelled ''Total Share'' or ''Number of Shares'' in Form 24. In the source forms this is expressed as a share count (e.g. 18,000) rather than a percentage. Extract the value exactly as printed, retaining the thousands separator, and keep it aligned with the corresponding shareholder row.
9. Director NRIC or Passport Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 991207-08-6447
   The identification number of each director, labelled ''ID/PASSPORT/REGISTRATION NO.'' on Form 24 and ''IC/Passport'' on Form 49. Accepts two formats: (a) Malaysian NRIC of 12 digits with hyphens, e.g. 991207-08-6447; (b) an alphanumeric passport number, e.g. A12345678. Extract as printed, keeping hyphens for NRIC. Where both an old and new IC number are shown, extract the new (12-digit) number.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (3, 'SSM Form 44', 'SSM Form 44, notifying the registered office address and office hours. A short single-purpose form containing little beyond the company name and the registered office address. Document type: Notice of Situation of Registered Office.', 'Financial Application', 'You are extracting structured data from a "SSM Form 44" document.
Document description: SSM Form 44, notifying the registered office address and office hours. A short single-purpose form containing little beyond the company name and the registered office address. Document type: Notice of Situation of Registered Office.

Fields to extract:

1. Entity Name  |  Type: Alphanumeric — e.g. AJS MAJU SERVICES SDN. BHD.
   The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.
2. Registered Address  |  Type: Alphanumeric — e.g. LOT 2-4-19
WISMA RAMPAI
TAMAN SRI RAMPAI, SETAPAK
KUALA LUMPUR
WILAYAH PERSEKUTUAN
   The company''s registered office address, labelled ''Registered Address'' / ''Alamat Berdaftar''. Capture the full multi-line address block including lot/unit, building, street, postcode, city and state. Preserve line breaks as they appear; do not reformat into a single line and do not append the country unless printed.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (4, 'SSM Form 49', 'SSM Form 49, listing officers of the company. Contains a company header block followed by a table of officers with name, ID/passport number, address, nationality and designation. The table mixes directors, secretaries and managers, so designation must be used to isolate directors. Document type: Return Giving Particulars in Register of Directors, Managers and Secretaries.', 'Financial Application', 'You are extracting structured data from a "SSM Form 49" document.
Document description: SSM Form 49, listing officers of the company. Contains a company header block followed by a table of officers with name, ID/passport number, address, nationality and designation. The table mixes directors, secretaries and managers, so designation must be used to isolate directors. Document type: Return Giving Particulars in Register of Directors, Managers and Secretaries.

Fields to extract:

1. Entity Name  |  Type: Alphanumeric — e.g. AJS MAJU SERVICES SDN. BHD.
   The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.
2. Business Registration Number  |  Type: Alphanumeric — e.g. 201401032382(1108466-M)
   The company registration number issued by SSM, labelled as ''Registration No.'', ''No. Pendaftaran'', or ''Company No.'' Modern format is a 12-digit number followed by the legacy number in brackets, e.g. 201401032382(1108466-M). Older documents may show only the legacy format (e.g. 1108466-M). Extract the value exactly as printed including brackets and hyphens.
3. Incorporation Date  |  Type: Datetime — e.g. 10-09-2024
   The date the company was incorporated, labelled as ''Date of Incorporation'', ''Tarikh Pemerbadanan'', or written in prose within the certificate (e.g. ''incorporated on the 10th day of September 2014''). Normalise to DD-MM-YYYY. If the document shows the date in long prose form, convert it.
4. Registered Address  |  Type: Alphanumeric — e.g. LOT 2-4-19
WISMA RAMPAI
TAMAN SRI RAMPAI, SETAPAK
KUALA LUMPUR
WILAYAH PERSEKUTUAN
   The company''s registered office address, labelled ''Registered Address'' / ''Alamat Berdaftar''. Capture the full multi-line address block including lot/unit, building, street, postcode, city and state. Preserve line breaks as they appear; do not reformat into a single line and do not append the country unless printed.
5. Business Address  |  Type: Alphanumeric — e.g. Lot 2-4-19, Wisma Rampai, Taman Sri Rampai, 53300 Setapak, Kuala Lumpur
   The address of the principal place of business, labelled ''Business Address'' / ''Alamat Perniagaan''. Capture the full multi-line block. If the form states it is the same as the registered office, extract the phrase as printed (e.g. ''Same as registered address'') rather than copying the registered address.
6. Business Nature  |  Type: Alphanumeric (multiple occurrences expected) — e.g. GENERAL CLEANING OF BUILDING
   The stated nature of the company''s business, labelled ''Nature of Business'' / ''Sifat Perniagaan''. Usually a short phrase in capitals. Extract the phrase as printed. If multiple activities are listed as separate line items, extract each as a separate value.
7. Director Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. MOHAMMAD FAIZ BIN AHMAD NEWAZ
   The full name of each director listed in Form 49, taken from the directors table. Extract every director row. Do not include company secretaries, managers, or auditors even where they appear in the same table - use the role/designation column to filter.
8. Director Address  |  Type: Alphanumeric (multiple occurrences expected) — e.g. NO 29, JALAN AMAN SERENIA 11/7 ANIRA,
BANDAR SERENIA
43900, SEPANG
SELANGGOR
   The residential address of each director, taken from the address column in the same table row as the director name. Must remain aligned with the corresponding Director Name. Capture the full multi-line block as printed.
9. Director NRIC or Passport Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 991207-08-6447
   The identification number of each director, labelled ''ID/PASSPORT/REGISTRATION NO.'' on Form 24 and ''IC/Passport'' on Form 49. Accepts two formats: (a) Malaysian NRIC of 12 digits with hyphens, e.g. 991207-08-6447; (b) an alphanumeric passport number, e.g. A12345678. Extract as printed, keeping hyphens for NRIC. Where both an old and new IC number are shown, extract the new (12-digit) number.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (5, 'SSM Form 9 & 28', 'A bundle of two certificates filed together. Form 9 is the Certificate of Incorporation, a short certificate stating the entity name, registration number and incorporation date in prose (e.g. ''incorporated on the 10th day of September 2014''). Form 28 follows in the same file. Treat as a single multi-document upload and extract from the Form 9 page unless stated otherwise. Document type: Certificate of Incorporation / Return of Allotment. This file may contain more than one document; treat it as a multi-document upload.', 'Financial Application', 'You are extracting structured data from a "SSM Form 9 & 28" document.
Document description: A bundle of two certificates filed together. Form 9 is the Certificate of Incorporation, a short certificate stating the entity name, registration number and incorporation date in prose (e.g. ''incorporated on the 10th day of September 2014''). Form 28 follows in the same file. Treat as a single multi-document upload and extract from the Form 9 page unless stated otherwise. Document type: Certificate of Incorporation / Return of Allotment. This file may contain more than one document; treat it as a multi-document upload.

Fields to extract:

1. Entity Name  |  Type: Alphanumeric — e.g. AJS MAJU SERVICES SDN. BHD.
   The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.
2. Business Registration Number  |  Type: Alphanumeric — e.g. 201401032382(1108466-M)
   The company registration number issued by SSM, labelled as ''Registration No.'', ''No. Pendaftaran'', or ''Company No.'' Modern format is a 12-digit number followed by the legacy number in brackets, e.g. 201401032382(1108466-M). Older documents may show only the legacy format (e.g. 1108466-M). Extract the value exactly as printed including brackets and hyphens.
3. Incorporation Date  |  Type: Datetime — e.g. 10-09-2024
   The date the company was incorporated, labelled as ''Date of Incorporation'', ''Tarikh Pemerbadanan'', or written in prose within the certificate (e.g. ''incorporated on the 10th day of September 2014''). Normalise to DD-MM-YYYY. If the document shows the date in long prose form, convert it.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (6, 'Form 32A', 'Form 32A, the instrument of transfer of securities. Header block carries the entity name and registration number. Layout is dense and label placement is inconsistent between SSM revisions. Document type: Transfer of Securities.', 'Financial Application', 'You are extracting structured data from a "Form 32A" document.
Document description: Form 32A, the instrument of transfer of securities. Header block carries the entity name and registration number. Layout is dense and label placement is inconsistent between SSM revisions. Document type: Transfer of Securities.

Fields to extract:

1. Entity Name  |  Type: Alphanumeric — e.g. AJS MAJU SERVICES SDN. BHD.
   The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.
2. Business Registration Number  |  Type: Alphanumeric — e.g. 201401032382(1108466-M)
   The company registration number issued by SSM, labelled as ''Registration No.'', ''No. Pendaftaran'', or ''Company No.'' Modern format is a 12-digit number followed by the legacy number in brackets, e.g. 201401032382(1108466-M). Older documents may show only the legacy format (e.g. 1108466-M). Extract the value exactly as printed including brackets and hyphens.
3. Incorporation Date  |  Type: Datetime — e.g. 10-09-2024
   The date the company was incorporated, labelled as ''Date of Incorporation'', ''Tarikh Pemerbadanan'', or written in prose within the certificate (e.g. ''incorporated on the 10th day of September 2014''). Normalise to DD-MM-YYYY. If the document shows the date in long prose form, convert it.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (7, 'Financial Statements (Sdn Bhd)', 'A full set of financial statements for a private limited company (Sdn Bhd), prepared under MPERS or MFRS: directors'' report, auditors'' report, statement of financial position, statement of profit or loss, statement of changes in equity, statement of cash flows, and notes. The statements present two or three comparative year columns side by side, so the per-year figures are extracted as a ''Financials By Year'' row group -- ONE row object per year column, keyed by that column''s Financial Statement Date, rather than as parallel arrays. Some figures appear only in the notes (notably depreciation and amortisation, and director advances), so the notes must be read. Four Boolean attributes record which constituent statements are present, since abridged or unaudited filings routinely omit the cash flow statement and the auditors'' report. Document type: Audited or Unaudited Financial Statements.', 'Financial Application', 'You are extracting structured data from a "Financial Statements (Sdn Bhd)" document.
Document description: A full set of financial statements for a private limited company (Sdn Bhd), prepared under MPERS or MFRS: directors'' report, auditors'' report, statement of financial position, statement of profit or loss, statement of changes in equity, statement of cash flows, and notes. The statements present two or three comparative year columns side by side, so the per-year figures are extracted as a ''Financials By Year'' row group -- ONE row object per year column, keyed by that column''s Financial Statement Date, rather than as parallel arrays. Some figures appear only in the notes (notably depreciation and amortisation, and director advances), so the notes must be read. Four Boolean attributes record which constituent statements are present, since abridged or unaudited filings routinely omit the cash flow statement and the auditors'' report. Document type: Audited or Unaudited Financial Statements.

Fields to extract:

1. Profit and Loss Statement Present  |  Type: Boolean — e.g. TRUE
   Whether the document contains a profit and loss statement. Accept any of: ''Statement of Profit or Loss'', ''Statement of Profit or Loss and Other Comprehensive Income'', ''Statement of Comprehensive Income'', ''Income Statement'', ''Profit and Loss Account'', ''Penyata Pendapatan''. Return TRUE if the statement itself appears, FALSE otherwise.
2. Document Type  |  Type: Alphanumeric — e.g. SSM Form 24
   Return the document type. Only return what is on this list [Company Act Section 14, SSM Form 24, SSM Form 44, SSM Form 49, SSM Form 9 & 28, Form 32A, Financial Statements (Sdn Bhd), Borang B, Bank Statements, MyKad (Director ID or Passport), Consent Form, Customer Information Form, Application Details, CTOS Report, CCRIS / CBM Report, Other]
3. Auditor''s Report Present  |  Type: Boolean — e.g. TRUE
   Whether the document contains an independent auditor''s report. Accept any of: ''Independent Auditors'' Report'', ''Report of the Auditors'', ''Laporan Juruaudit Bebas''. Return TRUE only where a signed report from an external audit firm is present. A directors'' report, statutory declaration, or a statement by directors does not count. FALSE indicates the accounts are unaudited, which is material to the credit assessment.
4. Balance Sheet Present  |  Type: Boolean — e.g. TRUE
   Whether the document contains a balance sheet. Accept any of the following headings as present: ''Statement of Financial Position'', ''Balance Sheet'', ''Penyata Kedudukan Kewangan''. Return TRUE if such a statement appears anywhere in the document, FALSE otherwise. This is a completeness check on the document, not a value extraction — a heading in the table of contents alone is not sufficient; the statement itself must be present.
5. Cash Flow Statement Present  |  Type: Boolean — e.g. FALSE
   Whether the document contains a cash flow statement. Accept any of: ''Statement of Cash Flows'', ''Cash Flow Statement'', ''Penyata Aliran Tunai''. Return TRUE if the statement itself appears, FALSE otherwise. Note that companies filing unaudited or abridged accounts frequently omit this statement, so FALSE is a common and valid result.
6. Financials By Year (repeating group — extract one row object per occurrence, with these columns):
   - Expenses or Opex or SG&A or Overheads  |  Type: Numeric — e.g. 816700
       Total operating expenses below the gross profit line, labelled ''Operating Expenses'', ''Administrative Expenses'', ''Selling and Distribution Expenses'', ''SG&A'', or ''Overheads''. Where several such lines are shown separately, sum them into a single value. Do NOT include Cost of Sales, Finance Costs, or Tax Expense. Store as a positive number even though it is presented as a deduction. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Other Income  |  Type: Numeric — e.g. 0
       Income from sources other than the principal revenue stream, labelled ''Other Income'', ''Other Operating Income'', or ''Sundry Income''. Includes interest income, rental income, and gains on disposal of assets. Presented as an addition; store as a positive number. Do not net it against expenses. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Costs or COGS  |  Type: Numeric — e.g. 3200000
       Direct cost of generating revenue, labelled ''Cost of Sales'', ''Cost of Goods Sold'', ''COGS'', or ''Direct Costs'' on the Statement of Profit or Loss. Presented as a deduction, often in parentheses; store as a positive number. Excludes operating expenses, finance costs and tax. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Operating Profit or EBIT  |  Type: Numeric — e.g. 803800
       Profit from operations before finance costs and tax, labelled ''Operating Profit'', ''Profit from Operations'', or ''EBIT''. Equals Gross Profit plus Other Income minus Operating Expenses. Many Malaysian SME statements omit this subtotal; leave blank rather than computing it from components. Negative if an operating loss is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Asset Value or Total Current Assets  |  Type: Numeric — e.g. 2466400
       Total assets from the Statement of Financial Position, the ''TOTAL ASSETS'' line, being Total Non-Current Assets plus Total Current Assets. If no consolidated total is presented, extract Total Current Assets and flag the omission. Must satisfy Total Assets = Total Liabilities + Total Equity. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - EBITDA  |  Type: Numeric — e.g. 905300
       Earnings before interest, tax, depreciation and amortisation. Extract only where the statement explicitly presents an ''EBITDA'' line, which is uncommon in Malaysian statutory accounts. Do not derive it by adding back Depreciation & Amortisation to EBIT — leave blank if not explicitly stated, and let the downstream model compute it from the component fields. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Profit Before Tax  |  Type: Numeric — e.g. 606700
       Profit before taxation from the Statement of Profit or Loss, labelled ''Profit Before Tax'', ''PBT'', or ''Profit Before Taxation''. For Islamic-compliant entities it may read ''Profit Before Zakat and Tax'' — extract that figure. Equals Operating Profit minus Finance Costs. Negative if a loss before tax is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Net Profit  |  Type: Numeric — e.g. 461092
       Profit after tax, labelled ''Profit After Tax'', ''Profit for the Financial Year'', or ''Net Profit''. Where a Statement of Comprehensive Income follows, take the profit for the year line, not total comprehensive income. Negative if a loss for the year is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Revenue or Turnover or Sales  |  Type: Numeric — e.g. 4820500
       The top line of the Statement of Profit or Loss. Extraction priority: (1) ''Revenue'', (2) ''Turnover'', (3) ''Sales''. Take the first of these that appears; do not fall back to Gross Profit for this field and never use Net Profit. Where revenue is disaggregated by segment in the notes, extract the consolidated total from the face of the statement. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Liability Value  |  Type: Numeric — e.g. 1405975
       Total liabilities from the Statement of Financial Position, being Total Non-Current Liabilities plus Total Current Liabilities. Many Malaysian statements omit an explicit ''TOTAL LIABILITIES'' line; where it is absent, sum the two subtotals. Do not derive it as Total Assets minus Total Equity unless neither subtotal is available. Store as a positive number. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Gross Profit  |  Type: Numeric — e.g. 1620500
       Gross profit from the Statement of Profit or Loss, labelled ''Gross Profit'' or ''Gross Loss''. Equals Revenue minus Cost of Sales. Where the statement is presented in a single-step format with no gross profit subtotal, leave blank rather than computing it. Negative if a gross loss is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Depreciation & Amortisation  |  Type: Numeric — e.g. 101500
       Depreciation of property, plant and equipment plus amortisation of intangible assets. Often not shown on the face of the Statement of Profit or Loss; take it from the ''Profit before tax is arrived at after charging'' note or the Statement of Cash Flows operating adjustments. Sum depreciation and amortisation into a single positive figure. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Advances Due to Director  |  Type: Numeric — e.g. 50000
       Amounts owed to or from directors, reported on the Statement of Financial Position and expanded in the related party notes. Usually sits under Current Liabilities as ''Amount due to directors'' or ''Directors'' loan account''. Where it instead appears under Current Assets as ''Amount due from directors'', store the value as a negative number. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Leave blank if the company reports no director advances; do not substitute zero. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Financial Statement Date  |  Type: Datetime — e.g. 31-12-2023
       The financial year end date for each comparative column of figures, taken from the column header of the Statement of Financial Position or Statement of Profit or Loss. Printed as ''Year ended 31 December 2023'', ''31.12.2023'', or simply ''2023'' above the RM column. Normalise to DD-MM-YYYY using the year end date. Extract one value per comparative column, left to right; this value keys every other figure in the same column.
   - Net Worth or Total Equity  |  Type: Numeric — e.g. 1060425
       Total shareholders'' equity from the Statement of Financial Position, labelled ''Total Equity'', ''Shareholders'' Funds'', or ''Net Worth''. Equals share capital plus retained earnings and reserves. If the statement shows an explicit ''Net Worth'' line separate from Total Equity, extract the Net Worth figure. May be negative where accumulated losses exceed share capital (capital deficiency). Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.
   - Financing Cost  |  Type: Numeric — e.g. 197100
       Interest and profit-sharing charges on borrowings, labelled ''Finance Costs'', ''Interest Expense'', or, for Islamic facilities, ''Profit Expense'' / ''Financing Cost''. Sits between Operating Profit and Profit Before Tax. Store as a positive number. Exclude interest income, which belongs in Other Income. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (8, 'Borang B', 'Borang B, the LHDN annual income tax return filed by a resident individual who carries on a business, covering one year of assessment. Structured as numbered parts on a fixed government form: Part A personal particulars, Part B statutory income and total income, Part C tax payable. The business source figures sit in Part B. Bilingual throughout, Malay label above English. Only the profit before tax figure is extracted here; do not take statutory income, aggregate income, or chargeable income, which are tax-adjusted and sit further down the same part. Document type: Individual Income Tax Return (Business Source).', 'Financial Application', 'You are extracting structured data from a "Borang B" document.
Document description: Borang B, the LHDN annual income tax return filed by a resident individual who carries on a business, covering one year of assessment. Structured as numbered parts on a fixed government form: Part A personal particulars, Part B statutory income and total income, Part C tax payable. The business source figures sit in Part B. Bilingual throughout, Malay label above English. Only the profit before tax figure is extracted here; do not take statutory income, aggregate income, or chargeable income, which are tax-adjusted and sit further down the same part. Document type: Individual Income Tax Return (Business Source).

Fields to extract:

1. Profit Before Tax  |  Type: Numeric (multiple occurrences expected) — e.g. 606700
   Profit before taxation from the Statement of Profit or Loss, labelled ''Profit Before Tax'', ''PBT'', or ''Profit Before Taxation''. For Islamic-compliant entities it may read ''Profit Before Zakat and Tax'' — extract that figure. Equals Operating Profit minus Finance Costs. Negative if a loss before tax is reported. Financial statements present two or three comparative year columns side by side; extract one value per year column and keep it aligned with the corresponding Financial Statement Date. Figures in parentheses are negative. If the statement is presented in thousands (RM''000), multiply by 1,000 before storing.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (9, 'Bank Statements', 'One or more months of a current or savings account statement, one bank per file. Real statements are a chronological listing of DAILY transactions and frequently have NO monthly summary block, so figures are taken by transcribing every transaction row (date, description, debit, credit, running balance). Monthly and yearly totals are computed downstream by the aggregation service, NOT summed here by the model. Header fields (bank name, account number, statement period) are Unique; each transaction is one row of the Transactions group. Malay labels may appear: Debit, Kredit, Baki (balance). Document type: Monthly Bank Account Statements.', 'Financial Application', 'You are extracting structured data from a "Bank Statements" document.
Document description: One or more months of a current or savings account statement, one bank per file. Real statements are a chronological listing of DAILY transactions and frequently have NO monthly summary block, so figures are taken by transcribing every transaction row (date, description, debit, credit, running balance). Monthly and yearly totals are computed downstream by the aggregation service, NOT summed here by the model. Header fields (bank name, account number, statement period) are Unique; each transaction is one row of the Transactions group. Malay labels may appear: Debit, Kredit, Baki (balance). Document type: Monthly Bank Account Statements.

Fields to extract:

1. Bank Name  |  Type: Alphanumeric — e.g. Standard Chartered Bank Malaysia Berhad
   The issuing bank of this statement, from the statement header or footer (e.g. ''Maybank Berhad'', ''CIMB Bank Berhad'', ''RHB Bank Berhad'', ''Public Bank Berhad'', ''Standard Chartered Bank Malaysia Berhad''). Return the full bank name as printed. One value per statement.
2. Document Type  |  Type: Alphanumeric — e.g. SSM Form 24
   Return the document type. Only return what is on this list [Company Act Section 14, SSM Form 24, SSM Form 44, SSM Form 49, SSM Form 9 & 28, Form 32A, Financial Statements (Sdn Bhd), Borang B, Bank Statements, MyKad (Director ID or Passport), Consent Form, Customer Information Form, Application Details, CTOS Report, CCRIS / CBM Report, Other]
3. Statement Period  |  Type: Alphanumeric — e.g. 01 Jan 2026 to 30 Jun 2026
   The date range the statement covers, from the period header (e.g. ''01 Jan 2026 to 30 Jun 2026''). Return as printed. One value per statement.
4. Account Number Masked  |  Type: Alphanumeric — e.g. ****4321
   The account number this statement covers, from the header. Keep any masking the bank already applies (e.g. ''****4321''); never unmask or invent digits. One value per statement.
5. Transactions (repeating group — extract one row object per occurrence, with these columns):
   - Transaction Date  |  Type: Datetime — e.g. 2026-01-23
       The posting/value date of one transaction row, from the transaction listing. You MUST return it in strict ISO format YYYY-MM-DD and nothing else -- convert any printed form to ISO (e.g. ''23 Jan 2026'' -> ''2026-01-23'', ''23/01/2026'' -> ''2026-01-23''); never return the day-month-name form or a range. One row per printed transaction line, in the order printed.
   - Transaction Debit  |  Type: Numeric — e.g. 3500.00
       The debit (money out / withdrawal / outflow) amount of one transaction row, as a positive number in RM. Null if the row is a credit rather than a debit.
   - Transaction Credit  |  Type: Numeric — e.g. 12000.00
       The credit (money in / deposit / inflow) amount of one transaction row, as a positive number in RM. Null if the row is a debit rather than a credit.
   - Transaction Description  |  Type: Alphanumeric — e.g. IBG TRANSFER TO SUPPLIER
       The narrative/description of one transaction row as printed (payee, reference, or transaction type).
   - Transaction Balance  |  Type: Numeric — e.g. 8500.00
       The running account balance printed after one transaction row, in RM. Negative if the account is overdrawn.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');

INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (10, 'MyKad (Director ID or Passport)', 'Photocopies or scans of the Malaysian MyKad, or of the passport biodata page for non-citizen directors, for every director of the applicant company. Usually several cards imaged on a single page, or one card per page, so treat the file as a multi-document upload and extract one name and one identification number per individual. MyKad text is printed in ALL CAPS over a patterned background and may be faint on photocopies. The card also carries an address on the reverse, which is not extracted here. Document type: Identity Document. This file may contain more than one document; treat it as a multi-document upload.', 'Financial Application', 'You are extracting structured data from a "MyKad (Director ID or Passport)" document.
Document description: Photocopies or scans of the Malaysian MyKad, or of the passport biodata page for non-citizen directors, for every director of the applicant company. Usually several cards imaged on a single page, or one card per page, so treat the file as a multi-document upload and extract one name and one identification number per individual. MyKad text is printed in ALL CAPS over a patterned background and may be faint on photocopies. The card also carries an address on the reverse, which is not extracted here. Document type: Identity Document. This file may contain more than one document; treat it as a multi-document upload.

Fields to extract:

1. Director Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. MOHAMMAD FAIZ BIN AHMAD NEWAZ
   The full name of each director listed in Form 49, taken from the directors table. Extract every director row. Do not include company secretaries, managers, or auditors even where they appear in the same table - use the role/designation column to filter.
2. Director NRIC or Passport Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 991207-08-6447
   The identification number of each director, labelled ''ID/PASSPORT/REGISTRATION NO.'' on Form 24 and ''IC/Passport'' on Form 49. Accepts two formats: (a) Malaysian NRIC of 12 digits with hyphens, e.g. 991207-08-6447; (b) an alphanumeric passport number, e.g. A12345678. Extract as printed, keeping hyphens for NRIC. Where both an old and new IC number are shown, extract the new (12-digit) number.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (11, 'Consent Form', 'A signed consent and declaration authorising the institution to verify the applicant''s information with SSM, BNM, CCRIS, CTOS, and LHDN. The entity name appears in the opening declaration paragraph. The signature section at the foot carries one block per signatory: a signature, the printed name beneath it, and the NRIC or passport number. Extract the printed names, not the signatures. Every director is normally required to sign, so a signatory count below the director count on SSM Form 49 is a discrepancy worth flagging. Document type: Consent and Declaration Form.', 'Financial Application', 'You are extracting structured data from a "Consent Form" document.
Document description: A signed consent and declaration authorising the institution to verify the applicant''s information with SSM, BNM, CCRIS, CTOS, and LHDN. The entity name appears in the opening declaration paragraph. The signature section at the foot carries one block per signatory: a signature, the printed name beneath it, and the NRIC or passport number. Extract the printed names, not the signatures. Every director is normally required to sign, so a signatory count below the director count on SSM Form 49 is a discrepancy worth flagging. Document type: Consent and Declaration Form.

Fields to extract:

1. Entity Name  |  Type: Alphanumeric — e.g. AJS MAJU SERVICES SDN. BHD.
   The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.
2. Director Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. MOHAMMAD FAIZ BIN AHMAD NEWAZ
   The full name of each director listed in Form 49, taken from the directors table. Extract every director row. Do not include company secretaries, managers, or auditors even where they appear in the same table - use the role/designation column to filter.
3. Director NRIC or Passport Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 991207-08-6447
   The identification number of each director, labelled ''ID/PASSPORT/REGISTRATION NO.'' on Form 24 and ''IC/Passport'' on Form 49. Accepts two formats: (a) Malaysian NRIC of 12 digits with hyphens, e.g. 991207-08-6447; (b) an alphanumeric passport number, e.g. A12345678. Extract as printed, keeping hyphens for NRIC. Where both an old and new IC number are shown, extract the new (12-digit) number.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (12, 'Customer Information Form', 'The institution''s own customer information form, completed by the applicant. Two distinct halves. The director section repeats one block per director and carries personal particulars: address, religion, education, marital status, spouse details, emergency contact, income, email, and years of industry experience; every attribute in that section is Multiple and must stay aligned with the director named in the same block. The company section appears once and carries premises, headcount, contact, and auditor details; those attributes are Unique. Fields are bilingual with tick boxes for the categorical entries, so an unselected box means the value is genuinely absent and should be left blank. Document type: Applicant Profile Form.', 'Financial Application', 'You are extracting structured data from a "Customer Information Form" document.
Document description: The institution''s own customer information form, completed by the applicant. Two distinct halves. The director section repeats one block per director and carries personal particulars: address, religion, education, marital status, spouse details, emergency contact, income, email, and years of industry experience; every attribute in that section is Multiple and must stay aligned with the director named in the same block. The company section appears once and carries premises, headcount, contact, and auditor details; those attributes are Unique. Fields are bilingual with tick boxes for the categorical entries, so an unselected box means the value is genuinely absent and should be left blank. Document type: Applicant Profile Form.

Fields to extract:

1. Director Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. MOHAMMAD FAIZ BIN AHMAD NEWAZ
   The full name of each director listed in Form 49, taken from the directors table. Extract every director row. Do not include company secretaries, managers, or auditors even where they appear in the same table - use the role/designation column to filter.
2. Director Address  |  Type: Alphanumeric (multiple occurrences expected) — e.g. NO 29, JALAN AMAN SERENIA 11/7 ANIRA,
BANDAR SERENIA
43900, SEPANG
SELANGGOR
   The residential address of each director, taken from the address column in the same table row as the director name. Must remain aligned with the corresponding Director Name. Capture the full multi-line block as printed.
3. Director Religion  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Islam
   The religion declared by a director, labelled ''Agama / Religion''. Typically a single word, and often a tick-box selection rather than free text: Islam, Buddha, Kristian, Hindu, Lain-lain. Extract the selected option as printed, without translating. Leave blank if no option is selected. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
4. Director Higher Education  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Ijazah Sarjana Muda
   The highest level of education attained by a director, labelled ''Pendidikan / Education'' or ''Taraf Pendidikan''. Usually a tick-box selection: SPM, STPM, Diploma, Ijazah / Degree, Sarjana / Master, PhD, Lain-lain. Where the form provides a free-text field for the institution or field of study, extract the qualification level only. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
5. Director Marital Status  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Married
   The marital status of a director, labelled ''Status Perkahwinan / Marital Status''. A tick-box selection: Bujang / Single, Berkahwin / Married, and sometimes Duda / Janda (widowed or divorced). Normalise to the English term: Single, Married, Widowed, Divorced. Where the status is Married, the spouse fields should also be populated. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
6. Director Spouse Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Nurul Aina binti Abdullah
   The full name of a director''s spouse, labelled ''Nama Pasangan / Spouse Name''. Only present where the director''s marital status is Married. Leave blank for unmarried directors rather than repeating the director''s own name. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
7. Director Spouse Contact Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 012-345 6789
   The contact telephone number of a director''s spouse, labelled ''No. Tel Pasangan''. Malaysian mobile format is 01X-XXX XXXX; landline is 0X-XXXX XXXX. Store exactly as written on the form, retaining spacing and hyphens. Do not prepend the +60 country code unless it is printed. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
8. Director Emergency Contact Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Zainab binti Hassan
   The full name of the person a director nominates to be contacted in an emergency, labelled ''Nama Waris'' or ''Emergency Contact''. This is distinct from the spouse field, though the same person may be named in both. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
9. Director Emergency Contact Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 019-876 5432
   The telephone number of a director''s nominated emergency contact, labelled ''No. Tel Waris''. Store exactly as written, retaining spacing and hyphens. Keep aligned with the Emergency Contact Name in the same block. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
10. Director Emergency Contact Relationship  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Ibu
   The relationship between a director and the nominated emergency contact, labelled ''Hubungan / Relationship''. Free text or a tick box; common values are Isteri / Wife, Suami / Husband, Ibu / Mother, Bapa / Father, Adik-beradik / Sibling, Anak / Child. Extract as printed without translating. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
11. Director Estimated Monthly Income  |  Type: Numeric (multiple occurrences expected) — e.g. 8500
   The director''s self-declared personal monthly income, labelled ''Anggaran Pendapatan Bulanan / Estimated Monthly Income''. Strip the RM prefix and any thousands separators and store as a number. This is personal income, not company revenue or the director''s drawings from the company. Where the form provides an income band rather than a figure, extract the lower bound and flag it. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
12. Director Email Address  |  Type: Alphanumeric (multiple occurrences expected) — e.g. faizal@gmail.com
   The personal email address of a director, labelled ''E-mel / Email'' within the director information block. Store exactly as written; do not normalise to lower case. Distinct from the company email address, though a director may supply the same value for both. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
13. Director Experience in Current Business  |  Type: Numeric (multiple occurrences expected) — e.g. 12
   The number of years a director has worked in the same line of business as the applicant company, labelled ''Pengalaman dalam bidang / Years of Experience''. Extract the numeric count of years only, discarding the word ''years'' or ''tahun''. Where a range is given, extract the lower bound. Where the form asks for months, convert to years and round down. Note this counts experience in the industry, which may exceed the age of the company itself. The form carries one block per director; extract one value per director and keep it aligned with the Director Name in the same block.
14. Company Current Office Address  |  Type: Alphanumeric — e.g. Lot 2-4-19, Wisma Rampai
Taman Sri Rampai
53300 Setapak
Kuala Lumpur
   The address of the company''s current operating premises, labelled ''Alamat Perniagaan / Business Address'' or ''Alamat Operasi''. Capture the full multi-line block. This may differ from the registered office address on SSM Form 44; where the form states the two are the same, extract the phrase as printed rather than copying the registered address across.
15. Company Office Status  |  Type: Alphanumeric — e.g. Rented
   Whether the company owns or rents its current office premises, labelled ''Status Premis''. A tick-box selection: Milik Sendiri / Owned, Sewa / Rented. Normalise to ''Owned'' or ''Rented''. Where the premises are rented, the monthly rent field should also be populated; where owned, the rent field is expected to be blank.
16. Company Office Monthly Rent  |  Type: Numeric — e.g. 3500
   The monthly rent paid on the company''s office premises, labelled ''Sewa Bulanan / Monthly Rent''. Strip the RM prefix and thousands separators and store as a number. Expected to be blank where Company Office Status is Owned; do not substitute zero, as a blank and a genuine zero carry different meanings for the credit assessment.
17. Company Age  |  Type: Numeric — e.g. 10
   The age of the company in years, labelled ''Umur Syarikat'' or ''Years in Operation''. Extract the numeric count of years only. Where the form leaves this blank, leave it blank rather than calculating it from the incorporation date; the downstream model can derive it. Where the form gives a range, extract the lower bound.
18. Company Number of Staff  |  Type: Numeric — e.g. 25
   The headcount of the company, labelled ''Bilangan Pekerja / Number of Employees''. Extract the total as an integer. Where the form splits headcount into permanent and contract or into full-time and part-time, sum the categories into a single total. Where a range is given, extract the lower bound.
19. Company Email Address  |  Type: Alphanumeric — e.g. admin@ajsmaju.com.my
   The company''s official contact email address, labelled ''E-mel Syarikat''. Store exactly as written; do not normalise to lower case. Where the form carries several email addresses, take the one in the company details section, not one from a director''s personal block.
20. Company Office Telephone  |  Type: Alphanumeric — e.g. 03-4142 5678
   The company''s office telephone number, labelled ''No. Tel Pejabat''. Malaysian landline format is 0X-XXXX XXXX; a mobile number may be given instead, in the format 01X-XXX XXXX. Store exactly as written on the form, retaining spacing and hyphens. Do not prepend the +60 country code unless it is printed.
21. Company Auditor Firm Name  |  Type: Alphanumeric — e.g. Tan & Associates PLT
   The registered name of the audit firm engaged by the company, labelled ''Nama Firma Juruaudit / Auditor''. Typically ends in ''PLT'', ''& Co.'', or ''Chartered Accountants''. Extract the firm name only, excluding the firm''s address or AF registration number where those appear on the same line.
22. Company Auditor Contact Person  |  Type: Alphanumeric — e.g. Tan Wei Ming
   The name of the individual contact at the audit firm, labelled ''Pegawai Bertanggungjawab'' or ''Contact Person''. This is a person''s name, not the firm name. Leave blank where the form names only the firm.
23. Company Auditor Contact Number  |  Type: Alphanumeric — e.g. 03-7728 1234
   The telephone number of the audit firm or its named contact person, labelled ''No. Tel Juruaudit''. Store exactly as written, retaining spacing and hyphens. Where both a firm line and a mobile number are given, extract the number printed against the auditor contact fields.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (13, 'Application Details', 'The financing application cover form. Captures how the application was referred, who to contact about it, the legal form of the business, the programme applied for, and the amount requested. Referrer and Business Entity Type are closed dropdowns and must return one of their listed options verbatim. The contact fields repeat, so they are Multiple and must stay row-aligned with each other. Additional Notes is free text and is captured verbatim rather than summarised. Document type: Financing Application Form.', 'Financial Application', 'You are extracting structured data from a "Application Details" document.
Document description: The financing application cover form. Captures how the application was referred, who to contact about it, the legal form of the business, the programme applied for, and the amount requested. Referrer and Business Entity Type are closed dropdowns and must return one of their listed options verbatim. The contact fields repeat, so they are Multiple and must stay row-aligned with each other. Additional Notes is free text and is captured verbatim rather than summarised. Document type: Financing Application Form.

Fields to extract:

1. Referrer  |  Type: Alphanumeric — e.g. Branch
   How the application reached the institution, captured as a closed set of exactly four values: Branch, Event, Personal, Other. This is a tick box or dropdown on the application form. Return one of the four values verbatim and nothing else. Where the selected option is Other, the Referrer Detail field carries the free text explanation. Do not infer the value from Referrer Detail if no option is selected.
2. Referrer Detail  |  Type: Alphanumeric — e.g. Kota Bharu Branch
   Free text elaborating on the Referrer selection: the branch name where Referrer is Branch, the event name and date where Referrer is Event, the introducing person''s name where Referrer is Personal, or an explanation where Referrer is Other. Extract the text exactly as written. Leave blank where the form provides no elaboration.
3. Main Contact Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Mohammad Faiz bin Ahmad Newaz
   The full name of each person nominated as a point of contact for this application. Usually one or two people, and usually but not always directors. Extract every name listed in the contact section. Keep aligned with the corresponding Main Contact Email and Main Contact Phone Number in the same row or block.
4. Main Contact Email  |  Type: Alphanumeric (multiple occurrences expected) — e.g. faiz@ajsmaju.com.my
   The email address of each nominated contact person, aligned to the Main Contact Name in the same row. Store exactly as written; do not normalise to lower case. Where a contact has no email listed, leave that entry blank rather than shifting the remaining emails up, which would break the alignment with the names.
5. Main Contact Phone Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 013-222 8899
   The telephone number of each nominated contact person, aligned to the Main Contact Name in the same row. Store exactly as written, retaining spacing and hyphens. Where a contact has no number listed, leave that entry blank rather than shifting the remaining numbers up.
6. Business Entity Type  |  Type: Alphanumeric — e.g. Sdn Bhd
   The legal form of the applicant business, captured as a closed set of exactly three values: Sdn Bhd, Sole Prop, Partnership. This is a tick box or dropdown on the application form. Return one of the three values verbatim. Note the form may label this field ''Business Address'', which is a labelling error on the form itself; match on the available options rather than on the label.
7. Proposed Program  |  Type: Alphanumeric — e.g. SME Working Capital Financing-i (Tawarruq)
   The financing programme or product the applicant is applying for, labelled ''Program / Skim Pembiayaan''. Extract the programme name exactly as printed, including any scheme code or Islamic contract name in brackets. Leave blank where the applicant has not nominated a programme.
8. Proposed Financing Amount  |  Type: Numeric — e.g. 1200000
   The gross financing amount requested by the applicant, labelled ''Jumlah Pembiayaan Dipohon / Financing Amount Requested''. Strip the RM prefix and thousands separators and store as a number. This is the facility amount applied for, not the net disbursement after fees, and not any amount subsequently approved.
9. Additional Notes  |  Type: Alphanumeric — e.g. Applicant requests expedited processing ahead of a Q4 contract award.
   Any free text the applicant or the receiving officer has written in the remarks, comments, or additional information section of the application form. Capture the full text verbatim, preserving line breaks. Do not summarise. Leave blank where the section is empty; do not fill it with text drawn from elsewhere on the form.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (14, 'CTOS Report', 'A CTOS credit report on the applicant company and its directors, generated by the bureau rather than filed by the applicant. Sections run: report header and generation date, company profile, directors and shareholders, litigation and legal records, trade references, and a summary score. The company profile supplies the entity name and incorporation date; the directors section supplies names and identification numbers; the litigation section supplies the legal cases. Legal cases are Multiple and a clean report yields none, which is a valid result rather than a failed extraction. Document type: Credit Bureau Report.', 'Financial Application', 'You are extracting structured data from a "CTOS Report" document.
Document description: A CTOS credit report on the applicant company and its directors, generated by the bureau rather than filed by the applicant. Sections run: report header and generation date, company profile, directors and shareholders, litigation and legal records, trade references, and a summary score. The company profile supplies the entity name and incorporation date; the directors section supplies names and identification numbers; the litigation section supplies the legal cases. Legal cases are Multiple and a clean report yields none, which is a valid result rather than a failed extraction. Document type: Credit Bureau Report.

Fields to extract:

1. Legal Case Report  |  Type: Alphanumeric (multiple occurrences expected) — e.g. Suit No. WA-B52-123-01/2023, Sessions Court Kuala Lumpur; Plaintiff: XYZ Supplies Sdn Bhd; Defendant: AJS Maju Services Sdn Bhd; Claim: RM45,200 for goods sold and delivered; Filed 12-01-2023; Status: Pending
   Each legal case disclosed in the litigation section of the CTOS report, covering both the company and its directors. Capture the full case entry as a single text block: case number, court, plaintiff, defendant, nature of the action, amount claimed, filing date, and status. Extract one value per case; a clean report has no cases, in which case return no values rather than a placeholder such as ''Nil'' or ''No records found''. Sections headed ''Bankruptcy'', ''Winding Up'', and ''Legal Suits'' all count as legal cases.
2. Entity Name  |  Type: Alphanumeric — e.g. AJS MAJU SERVICES SDN. BHD.
   The registered legal name of the company exactly as printed on the form, typically in the header block beside ''Name of Company'' / ''Nama Syarikat''. Retain the full suffix and punctuation as printed (e.g. ''SDN. BHD.'', ''BHD.''). Extract only the subject company, not the names of any other companies mentioned as shareholders or agents.
3. Incorporation Date  |  Type: Datetime — e.g. 10-09-2024
   The date the company was incorporated, labelled as ''Date of Incorporation'', ''Tarikh Pemerbadanan'', or written in prose within the certificate (e.g. ''incorporated on the 10th day of September 2014''). Normalise to DD-MM-YYYY. If the document shows the date in long prose form, convert it.
4. Director Name  |  Type: Alphanumeric (multiple occurrences expected) — e.g. MOHAMMAD FAIZ BIN AHMAD NEWAZ
   The full name of each director listed in Form 49, taken from the directors table. Extract every director row. Do not include company secretaries, managers, or auditors even where they appear in the same table - use the role/designation column to filter.
5. Director NRIC or Passport Number  |  Type: Alphanumeric (multiple occurrences expected) — e.g. 991207-08-6447
   The identification number of each director, labelled ''ID/PASSPORT/REGISTRATION NO.'' on Form 24 and ''IC/Passport'' on Form 49. Accepts two formats: (a) Malaysian NRIC of 12 digits with hyphens, e.g. 991207-08-6447; (b) an alphanumeric passport number, e.g. A12345678. Extract as printed, keeping hyphens for NRIC. Where both an old and new IC number are shown, extract the new (12-digit) number.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');
INSERT INTO templates (id, name, description, group_name, llm_prompt) VALUES (15, 'CCRIS / CBM Report', 'A CCRIS or CBM report from Bank Negara Malaysia disclosing the subject''s credit exposure. The outstanding credit section is a table with one row per credit facility, giving lender, facility type, approved limit, outstanding balance, instalment, and a twelve-digit conduct-of-account string from which Months in Arrears is read. Limit, Outstanding, Instalment and MIA are therefore Multiple and must stay aligned within each facility row. Probability of Default and total Liabilities are reported once for the subject and are Unique. Revolving facilities legitimately report no instalment. Document type: Central Credit Reference Information System Report.', 'Financial Application', 'You are extracting structured data from a "CCRIS / CBM Report" document.
Document description: A CCRIS or CBM report from Bank Negara Malaysia disclosing the subject''s credit exposure. The outstanding credit section is a table with one row per credit facility, giving lender, facility type, approved limit, outstanding balance, instalment, and a twelve-digit conduct-of-account string from which Months in Arrears is read. Limit, Outstanding, Instalment and MIA are therefore Multiple and must stay aligned within each facility row. Probability of Default and total Liabilities are reported once for the subject and are Unique. Revolving facilities legitimately report no instalment. Document type: Central Credit Reference Information System Report.

Fields to extract:

1. MIA  |  Type: Numeric (multiple occurrences expected) — e.g. 0
   Months in Arrears for a credit facility, reported in the CCRIS outstanding credit section as a twelve-digit string, one digit per month, most recent month first. Each digit is the number of months that instalment was in arrears; 0 means the facility was current. Extract the most recent month''s digit as the MIA for that facility, one value per facility, aligned to the same facility row as Limit, Outstanding and Instalment. A digit of 6 or above indicates an impaired account.
2. Probability of Default  |  Type: Numeric — e.g. 2.4
   The probability of default score for the subject, reported on the CBM or credit bureau summary page as a percentage or a decimal. Where reported as a percentage, store the numeric value without the percent sign (e.g. 2.4 for 2.4%). Where a score band or grade accompanies the figure, extract the numeric probability only. There is one PD for the subject, not one per facility.
3. Installment  |  Type: Numeric (multiple occurrences expected) — e.g. 4820
   The scheduled monthly instalment or repayment for a credit facility, reported in the CCRIS outstanding credit section. Store as a positive number in RM. One value per facility, aligned to the same facility row as Limit, Outstanding and MIA. Revolving facilities such as overdrafts and credit cards may report no instalment; leave those blank rather than entering zero.
4. Limit  |  Type: Numeric (multiple occurrences expected) — e.g. 500000
   The approved credit limit or original facility amount for a credit facility, reported in the CCRIS outstanding credit section. Store as a positive number in RM. One value per facility, aligned to the same facility row as Outstanding, Instalment and MIA. Term loans report the original approved amount; revolving facilities report the current limit.
5. Outstanding  |  Type: Numeric (multiple occurrences expected) — e.g. 312450
   The outstanding balance on a credit facility as at the CCRIS report date, reported in the outstanding credit section. Store as a positive number in RM. One value per facility, aligned to the same facility row as Limit, Instalment and MIA. May exceed the Limit where the facility is in excess.
6. Liabilities  |  Type: Numeric — e.g. 892300
   Total credit exposure for the subject: the sum of outstanding balances across all facilities disclosed in the CCRIS report. Where the report prints an explicit total line, extract that figure. Otherwise leave blank rather than summing the facility rows, so that the total remains traceable to the document. Store as a positive number in RM.

For each field above, also populate its entry in _locations with:
  real_page  — the actual sequential page number of the source document/PDF file, counting the
               first page as 1 regardless of any printed page numbers or cover/title pages
               (null if unknown). This is used to jump to the right page in the file.
  shown_page — the page number or label as it is printed/displayed on the page itself (e.g. a
               footer or header page number, which may be a roman numeral or differ from
               real_page due to unnumbered front matter) (null if no visible label).
  section    — nearest heading or section title on that page (null if unknown)
  document   — the source document name the value came from, if more than one was provided (null if unknown)

Return null for any field not found or unclear in the document.');

-- template_attributes: 119 rows
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (151, 1, 1, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (152, 1, 2, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (153, 1, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (154, 2, 3, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (155, 2, 4, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (156, 2, 5, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (157, 2, 6, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (158, 2, 8, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (159, 2, 9, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (160, 2, 10, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (161, 2, 11, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (162, 2, 14, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (163, 2, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (164, 3, 3, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (165, 3, 6, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (166, 3, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (167, 4, 3, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (168, 4, 4, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (169, 4, 5, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (170, 4, 6, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (171, 4, 7, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (172, 4, 8, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (173, 4, 12, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (174, 4, 13, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (175, 4, 14, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (176, 4, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (177, 5, 3, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (178, 5, 4, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (179, 5, 5, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (180, 5, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (181, 6, 3, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (182, 6, 4, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (183, 6, 5, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (184, 6, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (185, 7, 15, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (186, 7, 16, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (187, 7, 17, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (188, 7, 18, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (189, 7, 19, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (190, 7, 20, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (191, 7, 21, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (192, 7, 22, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (193, 7, 23, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (194, 7, 24, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (195, 7, 25, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (196, 7, 26, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (197, 7, 27, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (198, 7, 28, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (199, 7, 29, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (200, 7, 30, 'multiple', 'Financials By Year');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (201, 7, 31, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (202, 7, 32, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (203, 7, 33, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (204, 7, 34, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (205, 7, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (206, 8, 27, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (207, 8, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (270, 9, 80, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (271, 9, 81, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (272, 9, 82, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (273, 9, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (274, 9, 83, 'multiple', 'Transactions');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (275, 9, 84, 'multiple', 'Transactions');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (276, 9, 85, 'multiple', 'Transactions');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (277, 9, 86, 'multiple', 'Transactions');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (278, 9, 87, 'multiple', 'Transactions');
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (213, 10, 12, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (214, 10, 14, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (215, 10, 76, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (216, 10, 77, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (217, 10, 78, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (218, 10, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (219, 11, 3, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (220, 11, 12, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (221, 11, 14, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (222, 11, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (223, 12, 12, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (224, 12, 13, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (225, 12, 39, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (226, 12, 40, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (227, 12, 41, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (228, 12, 42, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (229, 12, 43, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (230, 12, 44, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (231, 12, 45, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (232, 12, 46, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (233, 12, 47, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (234, 12, 48, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (235, 12, 49, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (236, 12, 50, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (237, 12, 51, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (238, 12, 52, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (239, 12, 53, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (240, 12, 54, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (241, 12, 55, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (242, 12, 56, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (243, 12, 57, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (244, 12, 58, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (245, 12, 59, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (246, 12, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (247, 13, 60, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (248, 13, 61, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (249, 13, 62, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (250, 13, 63, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (251, 13, 64, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (252, 13, 65, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (253, 13, 66, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (254, 13, 67, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (255, 13, 68, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (256, 13, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (257, 14, 69, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (258, 14, 3, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (259, 14, 5, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (260, 14, 12, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (261, 14, 14, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (262, 14, 79, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (263, 15, 70, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (264, 15, 71, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (265, 15, 72, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (266, 15, 73, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (267, 15, 74, 'multiple', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (268, 15, 75, 'unique', NULL);
INSERT INTO template_attributes (id, template_id, attribute_id, frequency, row_group) VALUES (269, 15, 79, 'unique', NULL);

-- Re-sync auto-increment sequences after inserting explicit ids.
SELECT setval(pg_get_serial_sequence('attributes', 'id'), COALESCE((SELECT MAX(id) FROM attributes), 1));
SELECT setval(pg_get_serial_sequence('templates', 'id'), COALESCE((SELECT MAX(id) FROM templates), 1));
SELECT setval(pg_get_serial_sequence('template_attributes', 'id'), COALESCE((SELECT MAX(id) FROM template_attributes), 1));

COMMIT;
