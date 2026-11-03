add_executable(chunkbuffer2_test chunkbuffer2_test.c)

add_executable(bproto_test bproto_test.c)

add_executable(tcp_ring_buffer_test
    tcp_ring_buffer_test.c
    ../tun2socks/tcp_ring_buffer.c
)

add_executable(tcp_ring_buffer_stress_test
    tcp_ring_buffer_stress_test.c
    ../tun2socks/tcp_ring_buffer.c
)

add_executable(tcp_window_config_test tcp_window_config_test.c)

add_executable(udp_proto_test udp_proto_test.c)

add_executable(ipv4_fragment_test ipv4_fragment_test.c)

add_executable(udp_recv_flags_test udp_recv_flags_test.c)

add_executable(ipv6_udp_extension_test ipv6_udp_extension_test.c)

if (BUILDING_THREADWORK)
    add_executable(threadwork_test threadwork_test.c)
    target_link_libraries(threadwork_test threadwork)
endif ()
