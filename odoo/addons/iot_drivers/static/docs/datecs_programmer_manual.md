# FISCAL DEVICE DATECS - Programmer's Manual

**Models:** FMP-350X, FMP-55X, FP-700X, WP-500X, WP-50X, DP-25X, DP-150X

**Version:** 2.02, 2019

---

## Table of Contents

- [Description of the program interface](#description-of-the-program-interface)
- [Low level protocol](#low-level-protocol)
- [Message composition, syntax and meanings](#message-composition-syntax-and-meanings)
- [Command explanations](#command-explanations)
- [Commands Reference](#commands-reference)
- [Status bits](#status-bits)

---

## Description of the program interface

The fiscal device operates under the control of an application program, with which communicates via RS232 (USB or LAN) serial connection. The device executes a previously set of wrapped commands, arranged according to the type of the operations which have to be executed. The application program does not have a direct access to the resources of the fiscal device although it can detect data connected with the status of the fiscal device and the fiscal control unit.

---

## Low level protocol

### A) Protocol type - Master (Host) / Slave

The fiscal printer performs the commands sent by the Host and returns messages, which depend on the result. The fiscal printer cannot instigate asynchronous communications itself. Only responses to commands from the Host are sent to the Host. These messages are either wrapped or single byte control codes. The fiscal printer maintains the communication via the RS232 serial connection at baud rates of 1200, 2400, 4800, 9600, 19200, 38400, 57600 and 115200 b/s, 8N1.

### B) Sequence of the messages

Host sends a wrapped message, containing a command for the fiscal printer. ECR executes the requested operation and response with a wrapped message. Host has to wait for a response from the fiscal printer before to send another message. The protocol uses non-wrapped messages with a length one byte for processing of the necessary pauses and error conditions.

### C) Non-wrapped messages – time-out

When the transmitting of messages from the Host is normal, Slave answers not later than 60 ms either with a wrapped message or with a 1 byte code. Host must have 500 ms of time-out for receiving a message from Slave. If there is no message during this period of time the Host will transmit the message again with the same sequence number and the same command. After several unsuccessful attempts Host must indicate that there is either no connection to the fiscal printer or there is a hardware fault.

Non-wrapped messages consist of one byte and they are:

#### A) NAK 15H
This code is sent by Slave when an error in the control sum or the form of the received message is found. When Host receives a NAK it must again send a message with the same sequence number.

#### B) SYN 16H
This code is sent by Slave upon receiving a command which needs longer processing time. SYN is sent every 60 ms until the wrapped message is not ready for transmitting.

### D) Wrapped messages

#### a) Host to fiscal printer (Send)
```
<01><LEN><SEQ><CMD><DATA><05><BCC><03>
```

#### b) Fiscal printer to Host (Receive)
```
<01><LEN><SEQ><CMD><DATA><04><STATUS><05><BCC><03>
```

**Where:**

- **`<01>`** Preamble - 1 byte long. Value: 01H.
- **`<LEN>`** Number of bytes from `<01>` preamble (excluded) to `<05>` (included) plus the fixed offset of 20H. Length: 4 bytes. Each digit from the two bytes is sent after 30H is added to it. For example the sum 1AE3H is presented as 31H, 3AH, 3EH, 33H.
- **`<SEQ>`** Sequence number of the frame. Length: 1 byte. Value: 20H – FFH. The fiscal printer saves the same `<SEQ>` in the return message. If the ECR gets a message with the same `<SEQ>` as the last message received it will not perform any operation, but will repeat the last sent message.
- **`<CMD>`** The code of the command. Length: 4 byte. The fiscal printer saves the same `<CMD>` in the return message. If the fiscal printer receives a non-existing code it returns a wrapped message with zero length in the data field and sets the respective status bit. Each digit from the two bytes is sent after 30H is added to it.
- **`<DATA>`** Data. Length: 0-213 bytes for Host to fiscal printer, 0-218 bytes for Fiscal printer to Host. Value: 20H – FFH. The format and length of the field for storing data depends on the command.
- **`<04>`** Separator (only for fiscal printer-to-Host massages), Length: 1 byte. Value: 04H.
- **`<STATUS>`** The field with the current status of the fiscal device. Length: 8 bytes. Value: 80H-FFH.
- **`<05>`** Postamble. Length: 1 byte. Value:05H.
- **`<BCC>`** Control sum (0000H-FFFFH), Length: 4 bytes. Value of each byte: 30H-3FH. The sum includes between `<01>` preamble (excluded) to `<05>`. Each digit from the two bytes is sent after 30H is added to it.
- **`<03>`** Terminator, Length: 1 byte. Value: 03H.

---

## Message composition, syntax and meanings

a) The data field depends on the command.
b) The parameters sent to the fiscal printer may be separated with a `[\t]` and/or may have a fixed length.
c) The separator(`[\t]`) between the parameters shows that it is mandatory.
d) Some of the parameters are mandatory and others are optional. Optional parameters can be left empty, but after them must have separator (`[\t]`).

The symbols with ASCII codes under 32 (20H) have special meanings and their use is explained whenever necessary. If such a symbol has to be sent for some reason (for example in an ESCAPE-command to the display) it must be preceded by 16 (10H) with an added offset 40H.

**Example:** when we write `255,language[\t][\t][\t]` for the data field then in that field there will be `6C 61 6E 67 75 61 67 65 09 09 09` where each hexadecimal digit is an ASCII value.

---

## Command explanations

### Command syntax format:

```
{Parameter1}<SEP>{Parameter2}<SEP>{Parameter3}<SEP><DateTime><SEP>
```

**Note:** `<SEP>` - this tag must be inserted after each parameter to separate different parameters. Its value is `[\t]` (tab). It is the same for all commands.

#### Mandatory parameters:
- **Parameter1** - This parameter is mandatory, it must be filled
- **Parameter3** - This parameter is mandatory, it must be filled
  - **A** - Possible value of Parameter3; Answer(1) - if Parameter3 has value 'A' see Answer(1)
  - **B** - Possible value of Parameter3; Answer(2) - if Parameter3 has value 'B' see Answer(2)
- **DateTime** - Date and time format: DD-MM-YY hh:mm:ss DST
  - DD - Day
  - MM - Month
  - YY - Year
  - hh - Hours
  - mm - Minutes
  - ss - Seconds
  - DST - Text DST. If exist means that summer time is active.

#### Optional parameters:
- **Parameter2** - This parameter is optional it can be left blank, but separator must exist. Default: X

**Note:** If left blank parameter will be used with value, after "Default:" in this case 'X', but in some cases blank parameter may change the meaning of the command, which will be explained for each command.

**Answer(X)** - This is the default answer of the command.

#### Answer when command fail to execute:
```
{ErrorCode}<SEP>
```
- **ErrorCode** - Indicates an error code

---

## Commands Reference

### Command: 33 (21h) - Clears the external display

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>
```

**Note:** The command is not used on FMP-350X and FMP-55X

---

### Command: 35 (23h) - Displaying text on second line of the external display

**Parameters:**
```
{Text}<SEP>
```

**Mandatory parameters:**
- **Text** - Text to be sent directly to the external display (up to 20 symbols)

**Answer:**
```
{ErrorCode}<SEP>
```

**Note:** The command is not used on FMP-350X and FMP-55X

---

### Command: 38 (26h) - Opening a non-fiscal receipt

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>
```
- **ErrorCode** - Indicates an error code. If command passed, ErrorCode is 0
- **SlipNumber** - Current slip number (1...9999999)

---

### Command: 39 (27h) - Closing a non-fiscal receipt

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>
```
- **ErrorCode** - Indicates an error code. If command passed, ErrorCode is 0
- **SlipNumber** - Current slip number (1...9999999)

---

### Command: 42 (2Ah) - Printing of a free non-fiscal text

**Parameters:**
```
{Text}<SEP>{Bold}<SEP>{Italic}<SEP>{Hght}<SEP>{Underline}<SEP>{alignment}<SEP>
```

**Mandatory parameters:**
- **Text** - text of 0...XX symbols. XX depend of opened receipt type. XX = (PrintColumns-2)

**Optional parameters:**
- **Bold** - flag 0 or 1, 1 = print bold text; empty field = normal text
- **Italic** - flag 0 or 1, 1 = print italic text; empty field = normal text
- **Hght** - 0, 1 or 2. 0=normal height, 1=double height, 2=half height; empty field = normal height text
- **Underline** - flag 0 or 1, 1 = print underlined text; empty field = normal text
- **alignment** - 0, 1 or 2. 0=left alignment, 1=center, 2=right; empty field = left alignment

**Answer:**
```
{ErrorCode}<SEP>
```

---

### Command: 43 (2Bh) - Opening of storno documents

**Parameters:**
```
{OpCode}<SEP>{OpPwd}<SEP>{TillNmb}<SEP>{Storno}<SEP>{DocNum}<SEP>{DateTime}<SEP>{FMNumber}<SEP>{Invoice}<SEP>{ToInvoice}<SEP>{Reason}<SEP>{NSale}<SEP>
```

**Mandatory parameters:**
- **OpCode** - Operator number from 1...30
- **OpPwd** - Operator password, ascii string of digits. Length from 1...8
- **TillNmb** - Number of point of sale from 1...99999
- **Storno** - Reason for storno:
  - '0' - opens storno receipt. Reason "operator error"
  - '1' - opens storno receipt. Reason "refund"
  - '2' - opens storno receipt. Reason "tax base reduction"
- **DocNum** - Number of the original document (global 1...9999999)
- **FMNumber** - Fiscal memory number of the device the issued the original document
- **DateTime** - Date and time of the original document (format "DD-MM-YY hh:mm:ss DST")

**Optional parameters:**
- **Invoice** - If this parameter has value 'I' it opens an invoice storno/refund receipt
- **ToInvoice** - If Invoice is 'I' - Number of the invoice that this receipt is referred to
- **Reason** - If Invoice is 'I' - Reason for invoice storno/refund
- **NSale** - Unique sale number (21 chars "LLDDDDDD-CCCC-DDDDDDD", L[A-Z], C[0-9A-Za-z], D[0-9])

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>
```

---

### Command: 44 (2Ch) - Paper feed

**Parameters:**
```
{Lines}<SEP>
```

**Optional parameters:**
- **Lines** - Number of lines to feed from 1 to 99. Default: 1

**Answer:**
```
{ErrorCode}<SEP>
```

---

### Command: 45 (2Dh) - Check for mode connection with PC

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>
```

---

### Command: 46 (2Eh) - Paper cutting

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>
```

**Note:** The command is only used on FP-700X

---

### Command: 47 (2Fh) - Displaying text on upper line of the external display

**Parameters:**
```
{Text}<SEP>
```

**Mandatory parameters:**
- **Text** - Text to be sent directly to the external display (up to 20 symbols)

**Answer:**
```
{ErrorCode}<SEP>
```

**Note:** The command is not used on FMP-350X and FMP-55X

---

### Command: 48 (30h) - Open fiscal receipt

**Parameters:**

**Syntax 1:**
```
{OpCode}<SEP>{OpPwd}<SEP>{TillNmb}<SEP>{Invoice}<SEP>
```

**Syntax 2:**
```
{OpCode}<SEP>{OpPwd}<SEP>{NSale}<SEP>{TillNmb}<SEP>{Invoice}<SEP>
```

**Mandatory parameters:**
- **OpCode** - Operator number from 1...30
- **OpPwd** - Operator password, ascii string of digits. Length from 1...8
- **NSale** - Unique sale number (21 chars "LLDDDDDD-CCCC-DDDDDDD", L[A-Z], C[0-9A-Za-z], D[0-9])
- **TillNmb** - Number of point of sale from 1...99999
- **Invoice** - If this parameter has value 'I' it opens an invoice receipt. If left blank it opens fiscal receipt

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>
```

---

### Command: 49 (31h) - Registration of sale

**Parameters:**

**Syntax 1:**
```
{PluName}<SEP>{TaxCd}<SEP>{Price}<SEP>{Quantity}<SEP>{DiscountType}<SEP>{DiscountValue}<SEP>{Department}<SEP>
```

**Syntax 2:**
```
{PluName}<SEP>{TaxCd}<SEP>{Price}<SEP>{Quantity}<SEP>{DiscountType}<SEP>{DiscountValue}<SEP>{Department}<SEP>{Unit}<SEP>
```

**Mandatory parameters:**
- **PluName** - Name of product, up to 72 characters not empty string
- **TaxCd** - Tax code:
  - '1' - vat group A
  - '2' - vat group B
  - '3' - vat group C
  - '4' - vat group D
  - '5' - vat group E
  - '6' - vat group F
  - '7' - vat group G
  - '8' - vat group H
- **Price** - Product price, with sign '-' at void operations. Format: 2 decimals; up to *9999999.99
- **Department** - Number of the department 0..99; If '0' - Without department

**Optional parameters:**
- **Quantity** - Quantity of the product (default: 1.000); Format: 3 decimals; up to *999999.999
- **Unit** - Unit name, up to 6 characters not empty string

**Note:** Max value of Price * Quantity is *9999999.99

- **DiscountType** - type of discount:
  - '0' or empty - no discount
  - '1' - surcharge by percentage
  - '2' - discount by percentage
  - '3' - surcharge by sum
  - '4' - discount by sum
- **DiscountValue** - value of discount:
  - a number from 0.01 to 9999999.99 for sum operations
  - a number from 0.01 to 99.99 for percentage operations

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>
```

---

### Command: 50 (32h) - Return the active VAT rates

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>{nZreport}<SEP>{TaxA}<SEP>{TaxB}<SEP>{TaxC}<SEP>{TaxD}<SEP>{TaxE}<SEP>{TaxF}<SEP>{TaxG}<SEP>{TaxH}<SEP>{EntDate}<SEP>
```
- **ErrorCode** - Indicates an error code. If command passed, ErrorCode is 0
- **nZreport** - Number of first Z report
- **TaxX** - Value of Tax group X (0.00...99.99 taxable, 100.00=disabled)
- **EntDate** - Date of entry (format DD-MM-YY)

---

### Command: 51 (33h) - Subtotal

**Parameters:**
```
{Print}<SEP>{Display}<SEP>{DiscountType}<SEP>{DiscountValue}<SEP>
```

**Optional parameters:**
- **Print** - print out:
  - '0' - default, no print out
  - '1' - the sum of the subtotal will be printed out
- **Display** - Show the subtotal on the client display. Default: 0:
  - '0' - No display
  - '1' - The sum of the subtotal will appear on the display

**Note:** The option is not used on FMP-350X and FMP-55X

- **DiscountType** - type of discount:
  - '0' or empty - no discount
  - '1' - surcharge by percentage
  - '2' - discount by percentage
  - '3' - surcharge by sum
  - '4' - discount by sum
- **DiscountValue** - value of discount:
  - a number from 0.01 to 21474836.47 for sum operations
  - a number from 0.01 to 99.99 for percentage operations

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>{Subtotal}<SEP>{TaxA}<SEP>{TaxB}<SEP>{TaxC}<SEP>{TaxD}<SEP>{TaxE}<SEP>{TaxF}<SEP>{TaxG}<SEP>{TaxH}<SEP>
```

---

### Command: 53 (35h) - Payments and calculation of the total sum (TOTAL)

**Parameters:**

**Syntax 1:**
```
{PaidMode}<SEP>{Amount}<SEP>{Type}<SEP>
```

**Mandatory parameters:**
- **PaidMode** - Type of payment:
  - '0' - cash
  - '1' - credit card
  - '2' - debit card
  - '3' - other pay#3
  - '4' - other pay#4
  - '5' - other pay#5
- **Amount** - Amount to pay (0.00...9999999.99 or 0...999999999 depending dec point position)

**Optional parameters (with PinPad connected):**
- **Type** - Type of card payment. Only for payment with debit card:
  - '1' - with money
  - '12' - with points from loyal scheme

**Syntax 2:**
```
{PaidMode}<SEP>{Amount}<SEP>{Change}<SEP>
```
- **PaidMode** - Type of payment:
  - '6' - Foreign currency
- **Amount** - Amount to pay
- **Change** - Type of change. Only if PaidMode = '6':
  - '0' - current currency
  - '1' - foreign currency

**Answer:**
```
{ErrorCode}<SEP>{Status}<SEP>{Amount}<SEP>
```
- **Status** - Indicates an error:
  - 'D' - The command passed, return when the paid sum is less than the sum of the receipt. The residual sum due for payment is returned to Amount
  - 'R' - The command passed, return when the paid sum is greater than the sum of the receipt. A message "CHANGE" will be printed out and the change will be returned to Amount
- **Amount** - The sum tendered

---

### Command: 54 (36h) - Printing of a free fiscal text

**Parameters:**
```
{Text}<SEP>Bold<SEP>Italic<SEP>DoubleH<SEP>Underline<SEP>alignment<SEP>
```

**Mandatory parameters:**
- **Text** - text of 0...XX symbols, XX = PrintColumns-2

**Optional parameters:**
- **Bold** - flag 0 or 1, 1 = print bold text; empty field = normal text
- **Italic** - flag 0 or 1, 1 = print italic text; empty field = normal text
- **DoubleH** - flag 0 or 1, 1 = print double height text; empty field = normal text
- **Underline** - flag 0 or 1, 1 = print underlined text; empty field = normal text
- **alignment** - 0, 1 or 2. 0=left alignment, 1=center, 2=right; empty field = left alignment

**Answer:**
```
{ErrorCode}<SEP>
```

---

### Command: 56 (38h) - Close fiscal receipt

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>
```

---

### Command: 57 (39h) - Enter and print invoice data

**Parameters:**
```
{Seller}<SEP>{Receiver}<SEP>{Buyer}<SEP>{Address1}<SEP>{Address2}<SEP>{TypeTAXN}<SEP>{TAXN}<SEP>{VATN}<SEP>
```

**Mandatory parameters:**
- **TypeTAXN** - Type of client's tax number:
  - 0 - BULSTAT
  - 1 - EGN
  - 2 - LNCH
  - 3 - service number
- **TAXN** - Client's tax number. ascii string of digits 8...13

**Optional parameters:**
- **VATN** - VAT number of the client. 10...14 symbols
- **Seller** - Name of the client; 36 symbols max; if left blank prints empty space for hand-writing
- **Receiver** - Name of the receiver; 36 symbols max
- **Buyer** - Name of the buyer; 36 symbols max
- **Address1** - First line of the address; 36 symbols max
- **Address2** - Second line of the address; 36 symbols max

**Answer:**
```
{ErrorCode}<SEP>
```

---

### Command: 58 (3Ah) - Registering the sale of a programmed item

**Parameters:**
```
{PluCode}<SEP>{Quantity}<SEP>{Price}<SEP>{DiscountType}<SEP>{DiscountValue}<SEP>
```

**Mandatory parameters:**
- **PluCode** - The code of the item. from 1 to MAX_PLU. MAX_PLU: ECR-100000, Printer-3000

**Optional parameters:**
- **Quantity** - Quantity of the product (default: 1.000); Format: 3 decimals; up to *999999.999
- **DiscountType** - type of discount:
  - '0' or empty - no discount
  - '1' - surcharge by percentage
  - '2' - discount by percentage
  - '3' - surcharge by sum
  - '4' - discount by sum
- **DiscountValue** - value of discount:
  - a number from 0.01 to 9999999.99 for sum operations
  - a number from 0.01 to 100.00 for percentage operations

**Note:** Void operations are made by placing '-' before PluCode! In order to make void operation the Price parameter must be the same as the price at which the item was sold.

**Answer:**
```
{ErrorCode}<SEP>{SlipNumber}<SEP>
```

---

### Command: 60 (3Ch) - Cancel fiscal receipt

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>
```

---

### Command: 61 (3Dh) - Set date and time

**Parameters:**
```
{DateTime}<SEP>
```

**Mandatory parameters:**
- **DateTime** - Date and time in format: "DD-MM-YY hh:mm:ss DST"
  - DD - Day
  - MM - Month
  - YY - Year
  - hh - Hour
  - mm - Minute
  - ss - Second
  - DST - Text "DST" if exist time is Summer time

**Answer:**
```
{ErrorCode}<SEP>
```

---

### Command: 62 (3Eh) - Read date and time

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>{DateTime}<SEP>
```

---

### Command: 69 (45h) - Reports

**Parameters:**
```
{ReportType}<SEP>
```

**Mandatory parameters:**
- **ReportType** - Report type:
  - 'X' - X report
  - 'Z' - Z report
  - 'D' - Departments report
  - 'G' - Item groups report

**Answer:**
```
{ErrorCode}<SEP>{nRep}<SEP>{TotA}<SEP>{TotB}<SEP>{TotC}<SEP>{TotD}<SEP>{TotE}<SEP>{TotF}<SEP>{TotG}<SEP>{TotH}<SEP>{StorA}<SEP>{StorB}<SEP>{StorC}<SEP>{StorD}<SEP>{StorE}<SEP>{StorF}<SEP>{StorG}<SEP>{StorH}<SEP>
```

---

### Command: 74 (4Ah) - Reading the Status

**Parameters:** none

**Answer:**
```
{ErrorCode}<SEP>{StatusBytes}<SEP>
```
- **StatusBytes** - Status Bytes (See the description of the status bytes)

---

### Command: 90 (5Ah) - Diagnostic information

**Parameters:**
```
{Param}<SEP>
```

**Optional parameters:**
- none - Diagnostic information without firmware checksum
- '1' - Diagnostic information with firmware checksum

**Answer:**
```
{ErrorCode}<SEP>{Name}<SEP>{FwRev}<SEP>{FwDate}<SEP>{FwTime}<SEP>{Checksum}<SEP>{Sw}<SEP>{SerialNumber}<SEP>{FMNumber}<SEP>
```

---

## Status bits

The current status of the device is coded in field 8 bytes long which is sent within each message of the fiscal printer. Description of each byte in this field:

### Byte 0: General purpose
- 0.7 = 1 Always 1
- 0.6 = 1 Cover is open
- 0.5 = 1 General error - this is OR of all errors marked with #
- 0.4 = 1# Failure in printing mechanism
- 0.3 = 0 Always 0
- 0.2 = 1 The real time clock is not synchronized
- 0.1 = 1# Command code is invalid
- 0.0 = 1# Syntax error

### Byte 1: General purpose
- 1.7 = 1 Always 1
- 1.6 = 0 Always 0
- 1.5 = 0 Always 0
- 1.4 = 0 Always 0
- 1.3 = 0 Always 0
- 1.2 = 0 Always 0
- 1.1 = 1# Command is not permitted
- 1.0 = 1# Overflow during command execution

### Byte 2: General purpose
- 2.7 = 1 Always 1
- 2.6 = 0 Always 0
- 2.5 = 1 Nonfiscal receipt is open
- 2.4 = 1 EJ nearly full
- 2.3 = 1 Fiscal receipt is open
- 2.2 = 1 EJ is full
- 2.1 = 1 Near paper end
- 2.0 = 1# End of paper

### Byte 3: Not used
- 3.7 = 1 Always 1
- All other bits = 0 Always 0

### Byte 4: Fiscal memory
- 4.7 = 1 Always 1
- 4.6 = 1 Fiscal memory is not found or damaged
- 4.5 = 1 OR of all errors marked with '*' from Bytes 4 and 5
- 4.4 = 1* Fiscal memory is full
- 4.3 = 1 There is space for less then 60 reports in Fiscal memory
- 4.2 = 1 Serial number and number of FM are set
- 4.1 = 1 Tax number is set
- 4.0 = 1* Error when trying to access data stored in the FM

### Byte 5: Fiscal memory
- 5.7 = 1 Always 1
- 5.6 = 0 Always 0
- 5.5 = 0 Always 0
- 5.4 = 1 VAT are set at least once
- 5.3 = 1 Device is fiscalized
- 5.2 = 0 Always 0
- 5.1 = 1 FM is formatted
- 5.0 = 0 Always 0

### Byte 6: Not used
- 6.7 = 1 Always 1
- All other bits = 0 Always 0

### Byte 7: Not used
- 7.7 = 1 Always 1
- All other bits = 0 Always 0

---

## Disclaimer

The information contained in this document is subject to change without notice. No part of this document may be reproduced or transmitted, in any form or by any means, mechanical, electrical or electronic without the prior written permission of Datecs Ltd.

**© Datecs Ltd. 2019**
