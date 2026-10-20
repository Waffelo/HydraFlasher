/**
 * HydraFlasher firmware: serprog for the Raspberry Pi Pico with several sockets.
 *
 * Copyright (C) 2026 Jakub Heger <contact@waffelo.net>
 * Copyright (C) 2021, Mate Kukri <km@mkukri.xyz>
 * Based on "pico-serprog" by Thomas Roth <code@stacksmashing.net>
 *
 * Licensed under GPLv3 (see COPYING; third-party notices in NOTICE)
 *
 * Also based on stm32-vserprog:
 *  https://github.com/dword1511/stm32-vserprog
 *
 * Features added for HydraFlasher:
 *  - Multi chip-select support (10 sockets + broadcast),
 *  - LED status indicator,
 *  - CRC32 of a flash range on the Pico (DMA sniffer),
 *  - Reboot into the USB bootloader on command,
 *  - Number of CS lines and which of them are wired to a socket,
 *  - Status register polling on the Pico, double-buffered DMA reads.
 *
 */

#include <string.h>
#include "pico/stdlib.h"
#include "pico/time.h"
#include "hardware/spi.h"
#include "hardware/dma.h"
#include "pico/bootrom.h"
#include "tusb.h"
#include "serprog.h"

#define CDC_ITF     0           // USB CDC interface no

// Firmware version, reported by S_CMD_Q_PGMNAME as "HydraFlasher <version>" (16 bytes
// at most). Releases are tagged fw-v<version>, CI checks that the two match.
// 1.1: 10 chip selects, LEDs on GP16-18, S_CMD_Q_NUM_CS. Older firmware: "pico-serprog".
// 1.2: S_CMD_Q_CS_WIRED (which CS lines a socket board pulls up).
#define FW_VERSION  "1.3"

#define SPI_IF      spi0        // Which PL022 to use
#define SPI_BAUD    10000000    // Default baudrate (10 MHz)

#define SPI_MISO    4
#define SPI_MOSI    3
#define SPI_SCK     2

#define READ_CHUNK      1024          // SPI read chunk size (double-buffered via DMA)
#define MAX_WRITE_N     256           // Max data bytes per SPI op (one flash page)
#define MAX_READ_N      0x10000       // Max read length per SPI op (streamed in chunks)
#define TX_BUFFER_SIZE  (MAX_WRITE_N + 16)  // Page data + opcode/address headroom
#define SERIAL_BUFFER_SIZE 64

// Status-register polling shortcut: answer RDSR only once the chip is idle (or after this long)
#define SPI_CMD_RDSR    0x05
#define SR_WIP          0x01
#define STATUS_WAIT_US  5000

// LED status (right side of the board, physical pins 21 / 22 / 24)
#define LED_ORANGE  16   // BUSY (orange)
#define LED_GREEN   17   // PASS (green)
#define LED_RED     18   // FAIL (red)

// ────────────────────────────────────────────────
//  MULTI-CS SUPPORT – up to 10 physical chips + broadcast
// ────────────────────────────────────────────────
#define NUM_CS_PINS 10
// Select all CS at once. Always the index after the last pin: hosts learn both from
// S_CMD_Q_NUM_CS (firmware without that command has 6 pins and broadcasts on 6).
#define CS_BROADCAST NUM_CS_PINS


static const uint8_t cs_pins[NUM_CS_PINS] = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14};


static uint8_t active_cs_idx = 0;  // Defaults to CS0 if not specified

// ────────────────────────────────────────────────

static bool spi_enabled = false;
static uint32_t current_baud = SPI_BAUD;

static int dma_tx, dma_rx;
static const uint8_t dummy_tx = 0x00;

static void set_led_state(uint8_t state)
{
    gpio_put(LED_ORANGE, 0);
    gpio_put(LED_GREEN,  0);
    gpio_put(LED_RED,    0);

    switch (state)
    {
        case 0:     // Idle
            break;
        case 1:     // BUSY -> orange
            gpio_put(LED_ORANGE, 1);
            break;
        case 2:     // PASS -> green
            gpio_put(LED_GREEN, 1);
            break;
        case 3:     // FAIL -> red
            gpio_put(LED_RED, 1);
            break;
        default:
            break;
    }
}

static void enable_spi(uint baud)
{
    current_baud = spi_init(SPI_IF, baud);
    gpio_set_function(SPI_MISO, GPIO_FUNC_SPI);
    gpio_set_function(SPI_MOSI, GPIO_FUNC_SPI);
    gpio_set_function(SPI_SCK,  GPIO_FUNC_SPI);
    // Empty sockets then read as 0xFF instead of floating (used for socket detection)
    gpio_pull_up(SPI_MISO);
    spi_enabled = true;
}

static void disable_spi()
{
    gpio_init(SPI_MISO);
    gpio_init(SPI_MOSI);
    gpio_init(SPI_SCK);

    gpio_set_pulls(SPI_MISO, 0, 0);
    gpio_set_pulls(SPI_MOSI, 0, 0);
    gpio_set_pulls(SPI_SCK,  0, 0);

    spi_deinit(SPI_IF);
    spi_enabled = false;
}

static inline void cs_select(void) {
    sleep_us(1);
    if (active_cs_idx == CS_BROADCAST) {
        // Pull ALL chip-select pins low at the same time
        for (int i = 0; i < NUM_CS_PINS; i++) {
            gpio_put(cs_pins[i], 0);
        }
    } else {
        gpio_put(cs_pins[active_cs_idx], 0);
    }
    sleep_us(1);
}

static inline void cs_deselect(void) {
    sleep_us(1);
    if (active_cs_idx == CS_BROADCAST) {
        // Release ALL chip-select pins
        for (int i = 0; i < NUM_CS_PINS; i++) {
            gpio_put(cs_pins[i], 1);
        }
    } else {
        gpio_put(cs_pins[active_cs_idx], 1);
    }
    sleep_us(1);
}

// Which CS lines are wired to a socket. A socket board pulls each /CS up to 3V3
// (10 kOhm), which wins against the RP2040's internal pull-down (50-80 kOhm): with
// the pin as an input and the pull-down on, a wired line reads high, an open one low.
static uint16_t detect_wired_cs(void)
{
    uint16_t wired = 0;
    for (int i = 0; i < NUM_CS_PINS; i++) {
        uint pin = cs_pins[i];
        gpio_set_dir(pin, GPIO_IN);
        gpio_pull_down(pin);
        sleep_us(200);  // let the line settle (cable capacitance)
        if (gpio_get(pin))
            wired |= 1u << i;
        gpio_put(pin, 1);  // deselected before it drives again: no low glitch
        gpio_set_dir(pin, GPIO_OUT);
    }
    return wired;
}

static void dma_init(void)
{
    dma_tx = dma_claim_unused_channel(true);
    dma_rx = dma_claim_unused_channel(true);
}

// Start clocking len bytes from the flash into dst in the background.
// incr=false keeps writing the same byte (data only seen by the CRC sniffer).
static void spi_rx_dma_start(uint8_t *dst, uint32_t len, bool incr, bool sniff)
{
    dma_channel_config c = dma_channel_get_default_config(dma_tx);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_8);
    channel_config_set_dreq(&c, spi_get_dreq(SPI_IF, true));
    channel_config_set_read_increment(&c, false);
    channel_config_set_write_increment(&c, false);
    dma_channel_configure(dma_tx, &c, &spi_get_hw(SPI_IF)->dr, &dummy_tx, len, false);

    c = dma_channel_get_default_config(dma_rx);
    channel_config_set_transfer_data_size(&c, DMA_SIZE_8);
    channel_config_set_dreq(&c, spi_get_dreq(SPI_IF, false));
    channel_config_set_read_increment(&c, false);
    channel_config_set_write_increment(&c, incr);
    channel_config_set_sniff_enable(&c, sniff);
    dma_channel_configure(dma_rx, &c, dst, &spi_get_hw(SPI_IF)->dr, len, false);

    dma_start_channel_mask((1u << dma_tx) | (1u << dma_rx));
}

static inline void spi_read_dma_start(uint8_t *buf, uint32_t len)
{
    spi_rx_dma_start(buf, len, true, false);
}

// CRC32 (zlib/IEEE) of len bytes at addr on one socket, computed by the DMA
// sniffer while the data streams in at full SPI speed - nothing goes over USB.
static uint32_t flash_crc32(uint8_t cs, uint8_t addr_bytes, uint32_t addr, uint32_t len)
{
    static uint8_t sink;
    bool was_enabled = spi_enabled;
    uint8_t saved_cs = active_cs_idx;

    if (!was_enabled)
        enable_spi(current_baud);
    active_cs_idx = cs;

    uint8_t cmd[5];
    int n = 0;
    cmd[n++] = addr_bytes == 4 ? 0x13 : 0x03;   // READ / READ with 4-byte address
    if (addr_bytes == 4)
        cmd[n++] = addr >> 24;
    cmd[n++] = addr >> 16;
    cmd[n++] = addr >> 8;
    cmd[n++] = addr;

    cs_select();
    spi_write_blocking(SPI_IF, cmd, n);

    // Reflected CRC-32: bit-reversed input, reversed + inverted output, seed ~0
    dma_sniffer_enable(dma_rx, DMA_SNIFF_CTRL_CALC_VALUE_CRC32R, false);
    dma_sniffer_set_output_reverse_enabled(true);
    dma_sniffer_set_output_invert_enabled(true);
    dma_hw->sniff_data = 0xFFFFFFFF;

    spi_rx_dma_start(&sink, len, false, true);
    while (dma_channel_is_busy(dma_rx))
        tud_task();   // keep USB alive during long checks
    cs_deselect();

    uint32_t crc = dma_hw->sniff_data;
    dma_sniffer_disable();
    dma_sniffer_set_output_reverse_enabled(false);
    dma_sniffer_set_output_invert_enabled(false);

    active_cs_idx = saved_cs;
    if (!was_enabled)
        disable_spi();
    return crc;
}

static void spi_read_dma_wait(void)
{
    dma_channel_wait_for_finish_blocking(dma_rx);
}

static void wait_for_read(void)
{
    do tud_task();
    while (!tud_cdc_n_available(CDC_ITF));
}

static inline void readbytes_blocking(void *b, uint32_t len)
{
    uint8_t *ptr = b;
    while (len) {
        wait_for_read();
        uint32_t r = tud_cdc_n_read(CDC_ITF, ptr, len);
        ptr += r;
        len -= r;
    }
}

static inline uint8_t readbyte_blocking(void)
{
    wait_for_read();
    uint8_t b;
    tud_cdc_n_read(CDC_ITF, &b, 1);
    return b;
}

static void wait_for_write(void)
{
    do tud_task();
    while (!tud_cdc_n_write_available(CDC_ITF));
}

static inline void sendbytes_blocking(const void *b, uint32_t len)
{
    const uint8_t *ptr = b;
    while (len) {
        // Must service USB here, otherwise the TX FIFO never drains and
        // any transfer larger than the FIFO deadlocks.
        wait_for_write();
        uint32_t w = tud_cdc_n_write(CDC_ITF, ptr, len);
        ptr += w;
        len -= w;
    }
}

static inline void sendbyte_blocking(uint8_t b)
{
    wait_for_write();
    tud_cdc_n_write(CDC_ITF, &b, 1);
}

static void discard_bytes(uint32_t len)
{
    uint8_t tmp[64];
    while (len) {
        uint32_t chunk = len < sizeof tmp ? len : sizeof tmp;
        readbytes_blocking(tmp, chunk);
        len -= chunk;
    }
}

static inline void send_u24(uint32_t v)
{
    sendbyte_blocking(v & 0xFF);
    sendbyte_blocking((v >> 8) & 0xFF);
    sendbyte_blocking((v >> 16) & 0xFF);
}

static void command_loop(void)
{
    for (;;) {
        uint8_t cmd = readbyte_blocking();

        switch (cmd) {

        case S_CMD_NOP:
            sendbyte_blocking(S_ACK);
            break;

        case S_CMD_Q_IFACE:
            sendbyte_blocking(S_ACK);
            sendbyte_blocking(0x01);
            sendbyte_blocking(0x00);
            break;

        case S_CMD_Q_RDNMAXLEN:
            sendbyte_blocking(S_ACK);
            send_u24(MAX_READ_N);
            break;

        case S_CMD_Q_WRNMAXLEN:
            sendbyte_blocking(S_ACK);
            send_u24(MAX_WRITE_N);
            break;

        case S_CMD_Q_CMDMAP:
        {
            static const uint32_t cmdmap[8] = {
                [0] = (1UL << S_CMD_NOP)         |
                      (1UL << S_CMD_Q_IFACE)     |
                      (1UL << S_CMD_Q_RDNMAXLEN) |
                      (1UL << S_CMD_Q_WRNMAXLEN) |
                      (1UL << S_CMD_Q_CMDMAP)    |
                      (1UL << S_CMD_Q_PGMNAME)   |
                      (1UL << S_CMD_Q_SERBUF)    |
                      (1UL << S_CMD_Q_BUSTYPE)   |
                      (1UL << S_CMD_SYNCNOP)     |
                      (1UL << S_CMD_O_SPIOP)     |
                      (1UL << S_CMD_S_BUSTYPE)   |
                      (1UL << S_CMD_S_SPI_FREQ)  |
                      (1UL << S_CMD_S_PIN_STATE) |
                      (1UL << S_CMD_S_SPI_CS)    |
                      (1UL << S_CMD_S_SPI_MODE)  |
                      (1UL << S_CMD_S_CS_MODE),
                // Commands >= 32 live in the next word (1UL << 32 would overflow)
                [S_CMD_SET_LED / 32] = (1UL << (S_CMD_SET_LED % 32)) |
                                       (1UL << (S_CMD_BOOTSEL % 32)) |
                                       (1UL << (S_CMD_CRC32 % 32))   |
                                       (1UL << (S_CMD_Q_NUM_CS % 32)) |
                                       (1UL << (S_CMD_Q_CS_WIRED % 32)),
            };

            sendbyte_blocking(S_ACK);
            sendbytes_blocking((uint8_t *)cmdmap, sizeof cmdmap);
            break;
        }

        case S_CMD_Q_PGMNAME:
        {
            // Exactly 16 bytes, NUL-padded (flashprog prints at most 16 characters)
            static const char progname[16] = "HydraFlasher " FW_VERSION;
            _Static_assert(sizeof("HydraFlasher " FW_VERSION) <= 17, "name > 16 bytes");
            sendbyte_blocking(S_ACK);
            sendbytes_blocking(progname, sizeof progname);
            break;
        }

        case S_CMD_Q_SERBUF:
            sendbyte_blocking(S_ACK);
            sendbyte_blocking(SERIAL_BUFFER_SIZE & 0xFF);
            sendbyte_blocking((SERIAL_BUFFER_SIZE >> 8) & 0xFF);
            break;

        case S_CMD_Q_BUSTYPE:
            sendbyte_blocking(S_ACK);
            sendbyte_blocking(1 << 3); // BUS_SPI
            break;

        case S_CMD_SYNCNOP:
            sendbyte_blocking(S_NAK);
            sendbyte_blocking(S_ACK);
            break;

        case S_CMD_S_BUSTYPE:
        {
            uint8_t bus = readbyte_blocking();
            sendbyte_blocking((bus & (1 << 3)) ? S_ACK : S_NAK);
            break;
        }

        case S_CMD_S_PIN_STATE:
        {
            // flashprog enables drivers at start and disables them on exit,
            // so the SPI lines are high-Z while chips are swapped in the sockets.
            uint8_t enable = readbyte_blocking();
            if (enable && !spi_enabled)
                enable_spi(current_baud);
            else if (!enable && spi_enabled)
                disable_spi();
            sendbyte_blocking(S_ACK);
            break;
        }

        case S_CMD_S_SPI_CS:
        {
            uint8_t idx = readbyte_blocking();
            if (idx < NUM_CS_PINS) {
                active_cs_idx = idx;
                sendbyte_blocking(S_ACK);
            } else if (idx == CS_BROADCAST) {
                active_cs_idx = idx;  // broadcast mode
                sendbyte_blocking(S_ACK);
            } else {
                sendbyte_blocking(S_NAK);
            }
            break;
        }

        case S_CMD_S_SPI_MODE:
        case S_CMD_S_CS_MODE:
            (void)readbyte_blocking(); // we only support mode 0 and active-low CS
            sendbyte_blocking(S_ACK);
            break;

        case S_CMD_S_SPI_FREQ:
        {
            uint32_t want;
            readbytes_blocking(&want, 4);
            if (want >= 100000 && want <= 50000000) {
                if (spi_enabled)
                    current_baud = spi_set_baudrate(SPI_IF, want);
                else
                    current_baud = want;  // applied on next enable_spi()
                sendbyte_blocking(S_ACK);
                sendbytes_blocking(&current_baud, 4);
            } else {
                sendbyte_blocking(S_NAK);
            }
            break;
        }

        case S_CMD_O_SPIOP:
        {
            uint32_t slen = 0, rlen = 0;
            readbytes_blocking(&slen, 3);
            readbytes_blocking(&rlen, 3);

            uint8_t txbuf[TX_BUFFER_SIZE];
            static uint8_t rxbuf[2][READ_CHUNK];

            if (slen > sizeof txbuf || rlen > MAX_READ_N) {
                // Consume the payload so the stream stays in sync, then refuse
                discard_bytes(slen);
                sendbyte_blocking(S_NAK);
                break;
            }
            if (slen > 0)
                readbytes_blocking(txbuf, slen);

            if (!spi_enabled)
                enable_spi(current_baud);

            if (slen == 1 && rlen == 1 && txbuf[0] == SPI_CMD_RDSR) {
                // flashprog polls the status register over USB after every page
                // program / erase. Polling here until the chip is idle turns many
                // USB round trips into one.
                uint8_t sr;
                absolute_time_t deadline = make_timeout_time_us(STATUS_WAIT_US);
                do {
                    cs_select();
                    spi_write_blocking(SPI_IF, txbuf, 1);
                    spi_read_blocking(SPI_IF, 0x00, &sr, 1);
                    cs_deselect();
                } while ((sr & SR_WIP) && !time_reached(deadline));
                sendbyte_blocking(S_ACK);
                sendbyte_blocking(sr);
                break;
            }

            cs_select();

            if (slen > 0)
                spi_write_blocking(SPI_IF, txbuf, slen);

            sendbyte_blocking(S_ACK);

            // Double-buffered: DMA reads the next chunk from the flash while
            // the current one is pushed out over USB.
            uint32_t done = 0;
            int cur = 0;
            if (rlen > 0)
                spi_read_dma_start(rxbuf[cur], rlen < READ_CHUNK ? rlen : READ_CHUNK);
            while (done < rlen) {
                uint32_t chunk = rlen - done;
                if (chunk > READ_CHUNK) chunk = READ_CHUNK;
                spi_read_dma_wait();

                uint32_t next = rlen - done - chunk;
                if (next > 0)
                    spi_read_dma_start(rxbuf[cur ^ 1], next < READ_CHUNK ? next : READ_CHUNK);

                sendbytes_blocking(rxbuf[cur], chunk);
                done += chunk;
                cur ^= 1;
            }

            cs_deselect();
            break;
        }

        case S_CMD_SET_LED:
        {
            uint8_t state = readbyte_blocking();
            set_led_state(state);
            sendbyte_blocking(S_ACK);
            break;
        }

        case S_CMD_CRC32:
        {
            // cs(1) addr_bytes(1: 3 or 4) addr(4 LE) len(4 LE) -> ACK crc32(4 LE)
            uint8_t p[10];
            readbytes_blocking(p, sizeof p);
            uint32_t addr, len;
            memcpy(&addr, &p[2], 4);
            memcpy(&len, &p[6], 4);
            if (p[0] >= NUM_CS_PINS || (p[1] != 3 && p[1] != 4) || len == 0) {
                sendbyte_blocking(S_NAK);
                break;
            }
            uint32_t crc = flash_crc32(p[0], p[1], addr, len);
            sendbyte_blocking(S_ACK);
            sendbytes_blocking(&crc, 4);
            break;
        }

        case S_CMD_Q_NUM_CS:
            // Number of CS pins; the broadcast index is this same number
            sendbyte_blocking(S_ACK);
            sendbyte_blocking(NUM_CS_PINS);
            break;

        case S_CMD_Q_CS_WIRED:
        {
            // ACK + 2 bytes LE: bit n set = CS n has a pull-up (a socket is wired)
            uint16_t wired = detect_wired_cs();
            sendbyte_blocking(S_ACK);
            sendbyte_blocking(wired & 0xFF);
            sendbyte_blocking(wired >> 8);
            break;
        }

        case S_CMD_BOOTSEL:
            // Lets build.sh flash new firmware without pressing the BOOTSEL button
            sendbyte_blocking(S_ACK);
            tud_cdc_n_write_flush(CDC_ITF);
            for (int i = 0; i < 100; i++) {   // give the ACK ~10 ms to go out
                tud_task();
                sleep_us(100);
            }
            reset_usb_boot(0, 0);
            break;

        default:
            sendbyte_blocking(S_NAK);
            break;
        }

        tud_cdc_n_write_flush(CDC_ITF);
    }
}

int main()
{
    tusb_init();

    // ────────────────────────────────────────────────
    //  Initialize status LEDs
    // ────────────────────────────────────────────────
    gpio_init(LED_ORANGE);
    gpio_set_dir(LED_ORANGE, GPIO_OUT);
    gpio_put(LED_ORANGE, 0);

    gpio_init(LED_GREEN);
    gpio_set_dir(LED_GREEN, GPIO_OUT);
    gpio_put(LED_GREEN, 0);

    gpio_init(LED_RED);
    gpio_set_dir(LED_RED, GPIO_OUT);
    gpio_put(LED_RED, 0);

    set_led_state(0);

    // ────────────────────────────────────────────────
    //  Initialize all CS pins – high = deselected
    // ────────────────────────────────────────────────
    for (int i = 0; i < NUM_CS_PINS; i++) {
        gpio_init(cs_pins[i]);
        gpio_put(cs_pins[i], 1);
        gpio_set_dir(cs_pins[i], GPIO_OUT);
    }

    enable_spi(SPI_BAUD);
    dma_init();

    command_loop();

    // unreachable
    while (1) tight_loop_contents();
}
