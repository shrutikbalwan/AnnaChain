// UNPROVEN — compiles (env smoke); never run against an ATECC608B.
// See ac_atecc.h.
#ifdef ARDUINO
#include "ac_atecc.h"

#if __has_include(<ArduinoECCX08.h>)
#include <ArduinoECCX08.h>
#include <mbedtls/ecdsa.h>
#include <mbedtls/ecp.h>
#include <mbedtls/bignum.h>

namespace ac {

// One instance per address we try. ArduinoECCX08 keeps the address in the
// object, so probing two addresses means two objects.
static ECCX08Class* g_ecc = nullptr;

bool AteccSigner::begin() {
  addr_ = 0; locked_ = false;
  static const uint8_t candidates[] = {kAddrTrustGo, kAddrBlank};
  for (uint8_t a : candidates) {
    ECCX08Class* e = new ECCX08Class(wire_, a);
    if (e->begin()) { g_ecc = e; addr_ = a; break; }
    delete e;
  }
  if (!g_ecc) { err_ = "no ATECC608 answered at 0x35 or 0x60"; return false; }

  locked_ = g_ecc->locked();
  if (!locked_) {
    // Refuse rather than configure: locking is irreversible, and a wrong
    // configuration can make the key readable or the chip useless.
    err_ = "chip is unlocked (blank part): it must be provisioned first";
    return false;
  }
  err_ = "";
  return true;
}

String AteccSigner::serialNumber() {
  return g_ecc ? g_ecc->serialNumber() : String();
}

bool AteccSigner::publicKey(uint8_t pub[64]) {
  if (!g_ecc || !locked_) { err_ = "not started"; return false; }
  // GenKey in public-key mode: derives the public key from the private key in
  // the slot. It does not change the slot.
  if (!g_ecc->generatePublicKey(slot_, pub)) { err_ = "GenKey (public) failed"; return false; }
  return true;
}

bool AteccSigner::sign(const uint8_t digest[32], uint8_t sig[64]) {
  if (!g_ecc || !locked_) { err_ = "not started"; return false; }
  if (!g_ecc->ecSign(slot_, digest, sig)) { err_ = "Sign failed"; return false; }
  return true;
}

bool ecdsaVerifySoftware(const uint8_t pub[64], const uint8_t digest[32],
                         const uint8_t sig[64]) {
  mbedtls_ecp_group grp; mbedtls_ecp_point q; mbedtls_mpi r, s;
  mbedtls_ecp_group_init(&grp); mbedtls_ecp_point_init(&q);
  mbedtls_mpi_init(&r); mbedtls_mpi_init(&s);

  uint8_t point[65];
  point[0] = 0x04;                                   // uncompressed
  memcpy(point + 1, pub, 64);
  int rc = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_SECP256R1);
  if (!rc) rc = mbedtls_ecp_point_read_binary(&grp, &q, point, sizeof(point));
  if (!rc) rc = mbedtls_mpi_read_binary(&r, sig, 32);
  if (!rc) rc = mbedtls_mpi_read_binary(&s, sig + 32, 32);
  if (!rc) rc = mbedtls_ecdsa_verify(&grp, digest, 32, &q, &r, &s);

  mbedtls_mpi_free(&r); mbedtls_mpi_free(&s);
  mbedtls_ecp_point_free(&q); mbedtls_ecp_group_free(&grp);
  return rc == 0;
}

}  // namespace ac

#else   // no ArduinoECCX08 in this build: the driver is not linked in
namespace ac {
bool AteccSigner::begin() { err_ = "built without ArduinoECCX08"; return false; }
String AteccSigner::serialNumber() { return String(); }
bool AteccSigner::publicKey(uint8_t*) { return false; }
bool AteccSigner::sign(const uint8_t*, uint8_t*) { return false; }
bool ecdsaVerifySoftware(const uint8_t*, const uint8_t*, const uint8_t*) { return false; }
}  // namespace ac
#endif
#endif  // ARDUINO
