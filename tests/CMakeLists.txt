add_executable(chunkbuffer2_test chunkbuffer2_test.c)

add_executable(bproto_test bproto_test.c)

add_executable(tcp_ring_buffer_test
    tcp_ring_buffer_test.c
    ../tun2socks/tcp_ring_buffer.c
)

if (BUILDING_THREADWORK)
    add_executable(threadwork_test threadwork_test.c)
    target_link_libraries(threadwork_test threadwork)
endif ()
