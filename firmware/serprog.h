#ifndef SERPROG_H
#define SERPROG_H

/* According to Serial Flasher Protocol Specification - version 1 + multi-CS extension */
#define S_ACK 0x06
#define S_NAK 0x15

#define S_CMD_NOP		0x00	/* No operation					*/
#define S_CMD_Q_IFACE		0x01	/* Query interface version			*/
#define S_CMD_Q_CMDMAP		0x02	/* Query supported commands bitmap		*/
#define S_CMD_Q_PGMNAME		0x03	/* Query programmer name			*/
#define S_CMD_Q_SERBUF		0x04	/* Query Serial Buffer Size			*/
#define S_CMD_Q_BUSTYPE		0x05	/* Query supported bustypes			*/
#define S_CMD_Q_CHIPSIZE	0x06	/* Query supported chipsize (2^n format)	*/
#define S_CMD_Q_OPBUF		0x07	/* Query operation buffer size			*/
#define S_CMD_Q_WRNMAXLEN	0x08	/* Query Write to opbuf: Write-N maximum length */
#define S_CMD_R_BYTE		0x09	/* Read a single byte				*/
#define S_CMD_R_NBYTES		0x0A	/* Read n bytes					*/
#define S_CMD_O_INIT		0x0B	/* Initialize operation buffer			*/
#define S_CMD_O_WRITEB		0x0C	/* Write opbuf: Write byte with address		*/
#define S_CMD_O_WRITEN		0x0D	/* Write to opbuf: Write-N			*/
#define S_CMD_O_DELAY		0x0E	/* Write opbuf: udelay				*/
#define S_CMD_O_EXEC		0x0F	/* Execute operation buffer			*/
#define S_CMD_SYNCNOP		0x10	/* Special no-operation that returns NAK+ACK	*/
#define S_CMD_Q_RDNMAXLEN	0x11	/* Query read-n maximum length			*/
#define S_CMD_S_BUSTYPE		0x12	/* Set used bustype(s).				*/
#define S_CMD_O_SPIOP		0x13	/* Perform SPI operation.			*/
#define S_CMD_S_SPI_FREQ	0x14	/* Set SPI clock frequency			*/
#define S_CMD_S_PIN_STATE	0x15	/* Enable/disable output drivers		*/
#define S_CMD_S_SPI_CS		0x16	/* Set active Chip Select line			*/
#define S_CMD_S_SPI_MODE	0x17	/* Set SPI mode (0-3)				*/
#define S_CMD_S_CS_MODE		0x18	/* Set CS polarity / idle state			*/

#define S_CMD_SET_LED		0x20	/* Custom: set LED state (0=off,1=orange,2=green,3=red) */
#define S_CMD_BOOTSEL		0x21	/* Custom: reboot into the USB bootloader (RPI-RP2 drive) */
#define S_CMD_CRC32		0x22	/* Custom: CRC32 of a flash range on one socket		*/
#define S_CMD_Q_NUM_CS		0x23	/* Custom: number of CS pins (= broadcast index)	*/
#define S_CMD_Q_CS_WIRED	0x24	/* Custom: bitmap of CS lines with a pull-up (wired)	*/

#endif
